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
import os
import time
from datetime import UTC, datetime

import httpx

from app.core.config import settings
from app.core.engine import run_decision_loop
from app.core.recommender import recommend_strategies
from app.core.graph import agent_graph
from app.core.graph_state import AgentState

logger = logging.getLogger("crypto-agent")

# Assets to monitor
MONITORED_ASSETS = list(
    filter(None, os.getenv("MONITORED_ASSETS", "BTCUSDT,ETHUSDT,SOLUSDT").split(","))
)
MONITORED_ETF_ASSETS = list(
    filter(None, os.getenv("MONITORED_ETF_ASSETS", "SPY,QQQ").split(","))
)
MONITORED_STOCK_ASSETS = list(
    filter(None, os.getenv("MONITORED_STOCK_ASSETS", "AAPL,NVDA").split(","))
)

# Interval between analysis cycles (seconds)
CYCLE_INTERVAL = int(os.getenv("AGENT_CYCLE_INTERVAL", "300"))  # 5 min default

# Max backoff cycles for failing assets
_MAX_BACKOFF_CYCLES = 8  # ~40 minutes at 5-min intervals


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
        # Per-asset failure tracking for exponential backoff
        self._asset_failures: dict[str, int] = {}
        self._asset_skip_until: dict[str, int] = {}

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
            "etf_assets": MONITORED_ETF_ASSETS,
            "stock_assets": MONITORED_STOCK_ASSETS,
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

    def _should_skip_asset(self, asset: str) -> bool:
        """Check if asset should be skipped due to backoff."""
        skip_until = self._asset_skip_until.get(asset, 0)
        if self._cycle_count < skip_until:
            logger.info("asset_skipped_backoff", extra={
                "asset": asset,
                "skip_until_cycle": skip_until,
                "current_cycle": self._cycle_count,
            })
            return True
        return False

    def _record_asset_success(self, asset: str) -> None:
        """Reset failure counter on success."""
        self._asset_failures.pop(asset, None)
        self._asset_skip_until.pop(asset, None)

    def _record_asset_failure(self, asset: str) -> None:
        """Increment failure counter and set backoff."""
        failures = self._asset_failures.get(asset, 0) + 1
        self._asset_failures[asset] = failures
        backoff_cycles = min(2 ** failures, _MAX_BACKOFF_CYCLES)
        self._asset_skip_until[asset] = self._cycle_count + backoff_cycles
        logger.warning("asset_backoff_set", extra={
            "asset": asset,
            "consecutive_failures": failures,
            "backoff_cycles": backoff_cycles,
        })

    async def _run_crypto_asset(self, loop: asyncio.AbstractEventLoop, asset: str) -> None:
        """Run decision loop for a single crypto asset via LangGraph StateGraph."""
        if self._should_skip_asset(asset):
            return
        try:
            decision = await loop.run_in_executor(None, run_decision_loop, asset)
            phase_timings = {}
            for phase in (decision.decision_phases or []):
                if phase.name and phase.duration_ms is not None:
                    phase_timings[phase.name] = phase.duration_ms
            self._last_decisions[asset] = {
                "action": decision.action,
                "signal_score": decision.signal_score,
                "threshold_crossed": decision.threshold_crossed,
                "timestamp": decision.timestamp.isoformat() if decision.timestamp else None,
                "reasoning": (decision.reasoning or "")[:200],
                "phase_timings": phase_timings,
            }
            logger.info("agent_cycle_decision", extra={
                "asset": asset, "action": decision.action, "score": decision.signal_score,
                "phase_timings": phase_timings,
            })
            self._record_asset_success(asset)
        except Exception as exc:
            self._last_decisions[asset] = {
                "action": "ERROR",
                "error": str(exc)[:200],
                "timestamp": datetime.now(UTC).isoformat(),
            }
            logger.warning("agent_cycle_asset_error", extra={"asset": asset, "error": str(exc)})
            self._record_asset_failure(asset)

    async def _call_agent_decide(self, agent_base_url: str, asset: str, agent_name: str) -> None:
        """Call POST /agent/decide on an external agent (etf/stock). Non-fatal."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{agent_base_url.rstrip('/')}/agent/decide",
                    json={"asset": asset},
                )
                logger.info("external_agent_decide", extra={
                    "agent": agent_name, "asset": asset,
                    "status_code": resp.status_code,
                })
        except Exception as exc:
            logger.warning("external_agent_decide_failed", extra={
                "agent": agent_name, "asset": asset, "error": str(exc)[:100],
            })

    async def _run_cycle(self) -> None:
        cycle_start = time.monotonic()
        self._cycle_count += 1
        self._last_cycle = datetime.now(UTC)

        logger.info("agent_cycle_start", extra={
            "cycle": self._cycle_count,
            "assets": MONITORED_ASSETS,
        })

        loop = asyncio.get_event_loop()

        # --- Crypto assets: parallel processing with asyncio.gather ---
        crypto_tasks = [
            self._run_crypto_asset(loop, asset) for asset in MONITORED_ASSETS
        ]
        await asyncio.gather(*crypto_tasks, return_exceptions=True)

        # --- ETF agent decisions ---
        etf_base_url = os.getenv("ETF_AGENT_BASE_URL", "http://localhost:8021")
        etf_tasks = [
            self._call_agent_decide(etf_base_url, asset, "etf-agent")
            for asset in MONITORED_ETF_ASSETS
        ]
        if etf_tasks:
            await asyncio.gather(*etf_tasks, return_exceptions=True)

        # --- Stock agent decisions ---
        stock_base_url = os.getenv("STOCK_AGENT_BASE_URL", "http://localhost:8022")
        stock_tasks = [
            self._call_agent_decide(stock_base_url, asset, "stock-agent")
            for asset in MONITORED_STOCK_ASSETS
        ]
        if stock_tasks:
            await asyncio.gather(*stock_tasks, return_exceptions=True)

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

        # Shadow promotion check (every 12 cycles = ~1 hour at 5min interval)
        if self._cycle_count % 12 == 0:
            await self._check_shadow_promotions()

        # Auto-rebalance check (every 12 cycles = ~1 hour at 5min interval)
        if self._cycle_count % 12 == 0:
            await self._check_rebalance(loop)

        elapsed = time.monotonic() - cycle_start
        logger.info("agent_cycle_complete", extra={
            "cycle": self._cycle_count,
            "elapsed_ms": round(elapsed * 1000),
        })

    async def _check_shadow_promotions(self) -> None:
        """Check all SHADOW strategies and promote or deprecate them if ready."""
        try:
            registry_url = settings.strategy_registry_base_url.rstrip("/")
            async with httpx.AsyncClient(timeout=10.0) as client:
                # Fetch all shadow strategies
                resp = await client.get(f"{registry_url}/strategies/shadow")
                if resp.status_code != 200:
                    return
                shadow_strategies = resp.json()

            if not shadow_strategies:
                return

            logger.info("shadow_promotion_check", extra={
                "shadow_count": len(shadow_strategies),
            })

            async with httpx.AsyncClient(timeout=10.0) as client:
                for s in shadow_strategies:
                    strategy_id = s.get("id")
                    if not strategy_id:
                        continue
                    try:
                        resp = await client.post(
                            f"{registry_url}/strategies/{strategy_id}/shadow/promote"
                        )
                        if resp.status_code == 200:
                            result = resp.json()
                            outcome = result.get("outcome", "unknown")
                            if outcome in ("promoted", "deprecated"):
                                logger.info("shadow_promotion_result", extra={
                                    "strategy_id": strategy_id,
                                    "outcome": outcome,
                                    "metrics": result.get("shadow_metrics"),
                                })
                    except Exception as exc:
                        logger.warning("shadow_promote_call_failed", extra={
                            "strategy_id": strategy_id,
                            "error": str(exc)[:200],
                        })
        except Exception as exc:
            logger.debug("shadow_promotion_check_skipped", extra={"error": str(exc)[:100]})

    async def _check_rebalance(self, loop: asyncio.AbstractEventLoop) -> None:
        """Check portfolio and trigger rebalancing if needed."""
        try:
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
