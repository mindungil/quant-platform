"""Automatic agent scheduler.

Runs the decision loop periodically for configured assets.
The agent autonomously:
1. Fetches market data
2. Detects market regime
3. Selects optimal formula from memory
4. Makes trading decisions
5. Records outcomes for learning

No human intervention needed after initial setup.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime

from app.core.config import settings
from app.core.engine import run_decision_loop
from app.core.recommender import recommend_strategies

logger = logging.getLogger("crypto-agent")

# Assets to monitor
MONITORED_ASSETS = list(
    filter(None, __import__("os").getenv("MONITORED_ASSETS", "BTCUSDT,ETHUSDT,SOLUSDT").split(","))
)

# Interval between analysis cycles (seconds)
CYCLE_INTERVAL = int(__import__("os").getenv("AGENT_CYCLE_INTERVAL", "300"))  # 5 min default


class AgentScheduler:
    """Background scheduler that runs the agent decision loop automatically."""

    def __init__(self) -> None:
        self._running = False
        self._task: asyncio.Task | None = None
        self._last_cycle: datetime | None = None
        self._cycle_count = 0
        self._last_decisions: dict[str, dict] = {}
        self._last_recommendations: dict[str, list] = {}
        self._errors: list[dict] = []

    @property
    def status(self) -> dict:
        return {
            "running": self._running,
            "cycle_count": self._cycle_count,
            "last_cycle": self._last_cycle.isoformat() if self._last_cycle else None,
            "cycle_interval_seconds": CYCLE_INTERVAL,
            "monitored_assets": MONITORED_ASSETS,
            "last_decisions": {
                asset: {
                    "action": d.get("action"),
                    "signal_score": d.get("signal_score"),
                    "timestamp": d.get("timestamp"),
                }
                for asset, d in self._last_decisions.items()
            },
            "recent_errors": self._errors[-5:],
        }

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("agent_scheduler_started", extra={
            "interval": CYCLE_INTERVAL,
            "assets": MONITORED_ASSETS,
        })

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("agent_scheduler_stopped")

    async def _loop(self) -> None:
        # Initial delay to let services start up
        await asyncio.sleep(10)

        while self._running:
            try:
                await self._run_cycle()
            except Exception as exc:
                self._errors.append({
                    "time": datetime.now(UTC).isoformat(),
                    "error": str(exc),
                })
                if len(self._errors) > 50:
                    self._errors = self._errors[-50:]
                logger.exception("agent_cycle_error")

            # Wait for next cycle
            await asyncio.sleep(CYCLE_INTERVAL)

    async def _run_cycle(self) -> None:
        cycle_start = time.monotonic()
        self._cycle_count += 1
        self._last_cycle = datetime.now(UTC)

        logger.info("agent_cycle_start", extra={
            "cycle": self._cycle_count,
            "assets": MONITORED_ASSETS,
        })

        loop = asyncio.get_event_loop()

        for asset in MONITORED_ASSETS:
            try:
                # Run decision loop in thread (it's synchronous)
                decision = await loop.run_in_executor(
                    None, run_decision_loop, asset
                )
                self._last_decisions[asset] = {
                    "action": decision.action,
                    "signal_score": decision.signal_score,
                    "threshold_crossed": decision.threshold_crossed,
                    "timestamp": decision.timestamp.isoformat() if decision.timestamp else None,
                    "reasoning": (decision.reasoning or "")[:200],
                }
                logger.info("agent_cycle_decision", extra={
                    "asset": asset,
                    "action": decision.action,
                    "score": decision.signal_score,
                })
            except Exception as exc:
                self._last_decisions[asset] = {
                    "action": "ERROR",
                    "error": str(exc)[:200],
                    "timestamp": datetime.now(UTC).isoformat(),
                }
                logger.warning("agent_cycle_asset_error", extra={
                    "asset": asset,
                    "error": str(exc),
                })

        # Also update recommendations
        for asset in MONITORED_ASSETS:
            try:
                recs = await loop.run_in_executor(
                    None, recommend_strategies, asset, "crypto", 3
                )
                self._last_recommendations[asset] = [
                    {"name": r.name, "confidence": r.confidence, "formula": r.formula_name}
                    for r in recs
                ]
            except Exception:
                pass

        # Auto-rebalance check (every 12 cycles = ~1 hour at 5min interval)
        if self._cycle_count % 12 == 0:
            await self._check_rebalance(loop)

        elapsed = time.monotonic() - cycle_start
        logger.info("agent_cycle_complete", extra={
            "cycle": self._cycle_count,
            "elapsed_ms": round(elapsed * 1000),
        })

    async def _check_rebalance(self, loop: asyncio.AbstractEventLoop) -> None:
        """Check portfolio and trigger rebalancing if needed."""
        try:
            import httpx
            portfolio_url = settings.portfolio_service_base_url.rstrip("/")
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{portfolio_url}/portfolio/bootstrap")
                if resp.status_code != 200:
                    return
                portfolio = resp.json()

            if portfolio.get("rebalance_needed"):
                logger.info("auto_rebalance_triggered", extra={
                    "total_exposure": portfolio.get("total_exposure"),
                    "largest_position": portfolio.get("largest_position"),
                })
                async with httpx.AsyncClient(timeout=30.0) as client:
                    resp = await client.post(
                        f"{portfolio_url}/portfolio/bootstrap/optimize",
                        json={"method": "risk_parity"},
                    )
                    if resp.status_code == 200:
                        logger.info("auto_rebalance_complete", extra={"result": resp.json()})
                    else:
                        logger.warning("auto_rebalance_failed", extra={"status": resp.status_code})
        except Exception as exc:
            logger.debug("rebalance_check_skipped", extra={"error": str(exc)[:100]})


scheduler = AgentScheduler()
