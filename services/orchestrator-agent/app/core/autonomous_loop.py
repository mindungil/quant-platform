"""KAIROS-style autonomous orchestrator loop.

Periodically checks pipeline health, agent statuses, strategy drift,
and portfolio drawdown, then publishes a NATS tick event.
"""
from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timezone

import httpx

from shared.logging import get_logger

UTC = timezone.utc
logger = get_logger("orchestrator-loop")

STRATEGY_REGISTRY_BASE_URL = os.getenv("STRATEGY_REGISTRY_BASE_URL", "http://localhost:8005")
PORTFOLIO_SERVICE_BASE_URL = os.getenv("PORTFOLIO_SERVICE_BASE_URL", "http://localhost:8009")
NATS_URL = os.getenv("NATS_URL", "nats://localhost:4222")
LOOP_ENABLED = os.getenv("ORCHESTRATOR_LOOP_ENABLED", "true").lower() == "true"
LOOP_INTERVAL = int(os.getenv("ORCHESTRATOR_LOOP_INTERVAL_SECONDS", "300"))

DRAWDOWN_ALERT_THRESHOLD = 0.10  # 10%


class AutonomousLoop:
    """Background loop that monitors the platform every *interval_seconds*."""

    def __init__(self, interval_seconds: int = 300) -> None:
        self.interval_seconds = interval_seconds
        self._task: asyncio.Task | None = None
        self._running = False
        self._last_tick_summary: dict | None = None
        self._started_at: datetime | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._started_at = datetime.now(UTC)
        self._task = asyncio.create_task(self._run_loop())
        logger.info("autonomous_loop_started", extra={"interval": self.interval_seconds})

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("autonomous_loop_stopped")

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def _run_loop(self) -> None:
        while self._running:
            try:
                await self._tick()
            except Exception as exc:
                logger.error("autonomous_tick_error", extra={"error": str(exc)[:200]})
            await asyncio.sleep(self.interval_seconds)

    async def _tick(self) -> None:
        """Single monitoring tick — called every *interval_seconds*."""
        t0 = time.monotonic()
        summary: dict = {
            "timestamp": datetime.now(UTC).isoformat(),
            "pipeline_health": None,
            "agent_statuses": {},
            "strategy_drift_warnings": [],
            "drawdown_alert": False,
            "errors": [],
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            # 1. Pipeline health
            summary["pipeline_health"] = await self._check_pipeline(client)

            # 2. Agent statuses
            summary["agent_statuses"] = await self._check_agents(client)

            # 3. Strategy drift
            summary["strategy_drift_warnings"] = await self._check_strategy_drift(client)

            # 4. Drawdown
            summary["drawdown_alert"] = await self._check_drawdown(client)

            # 5. Publish NATS event
            await self._publish_nats_tick(summary)

        summary["duration_ms"] = round((time.monotonic() - t0) * 1000, 2)
        self._last_tick_summary = summary
        logger.info("autonomous_tick_completed", extra={
            "duration_ms": summary["duration_ms"],
            "drift_warnings": len(summary["strategy_drift_warnings"]),
            "drawdown_alert": summary["drawdown_alert"],
        })

    # ------------------------------------------------------------------
    # Sub-checks
    # ------------------------------------------------------------------

    async def _check_pipeline(self, client: httpx.AsyncClient) -> dict:
        try:
            resp = await client.get("http://localhost:8000/pipeline/health")
            if resp.status_code == 200:
                return resp.json()
        except Exception as exc:
            logger.warning("pipeline_health_check_failed", extra={"error": str(exc)[:100]})
        return {"status": "unknown"}

    async def _check_agents(self, client: httpx.AsyncClient) -> dict:
        from app.core.engine import get_all_agent_statuses
        try:
            return get_all_agent_statuses()
        except Exception as exc:
            logger.warning("agent_status_check_failed", extra={"error": str(exc)[:100]})
            return {}

    async def _check_strategy_drift(self, client: httpx.AsyncClient) -> list[dict]:
        warnings: list[dict] = []
        for asset_type in ("crypto", "etf", "stock"):
            try:
                resp = await client.get(
                    f"{STRATEGY_REGISTRY_BASE_URL}/strategies",
                    params={"asset_type": asset_type},
                )
                if resp.status_code != 200:
                    continue
                strategies = resp.json()
                if isinstance(strategies, list):
                    for s in strategies:
                        status = s.get("status", "")
                        drift = s.get("drift_alert", False)
                        if status == "DEPRECATED" or drift:
                            warning = {
                                "asset_type": asset_type,
                                "strategy_id": s.get("id", "?"),
                                "strategy_name": s.get("name", "?"),
                                "reason": f"status={status}" if status == "DEPRECATED" else "drift_alert",
                            }
                            warnings.append(warning)
                            logger.warning("strategy_drift_detected", extra=warning)
            except Exception as exc:
                logger.warning("strategy_drift_check_failed", extra={
                    "asset_type": asset_type, "error": str(exc)[:100],
                })
        return warnings

    async def _check_drawdown(self, client: httpx.AsyncClient) -> bool:
        try:
            resp = await client.get(f"{PORTFOLIO_SERVICE_BASE_URL}/portfolio/system")
            if resp.status_code == 200:
                data = resp.json()
                total_drawdown = abs(float(data.get("total_drawdown", 0.0)))
                if total_drawdown > DRAWDOWN_ALERT_THRESHOLD:
                    logger.warning("drawdown_alert", extra={
                        "total_drawdown": total_drawdown,
                        "threshold": DRAWDOWN_ALERT_THRESHOLD,
                    })
                    return True
        except Exception as exc:
            logger.warning("drawdown_check_failed", extra={"error": str(exc)[:100]})
        return False

    async def _publish_nats_tick(self, summary: dict) -> None:
        try:
            import nats as nats_lib
            nc = await nats_lib.connect(NATS_URL)
            import json
            await nc.publish(
                "orchestrator.tick.completed",
                json.dumps(summary, default=str).encode(),
            )
            await nc.flush()
            await nc.close()
        except Exception as exc:
            logger.debug("nats_tick_publish_failed", extra={"error": str(exc)[:100]})

    # ------------------------------------------------------------------
    # Public accessors
    # ------------------------------------------------------------------

    def get_last_tick_summary(self) -> dict:
        return self._last_tick_summary or {}

    def get_status(self) -> dict:
        return {
            "running": self._running,
            "interval_seconds": self.interval_seconds,
            "started_at": self._started_at.isoformat() if self._started_at else None,
            "last_tick": self._last_tick_summary,
        }
