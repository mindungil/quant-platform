"""Shadow trading recorder.

Maintains an in-memory ledger of shadow fills per strategy and computes
Sharpe / max DD / win rate on demand. The order service calls
`record_fill()` after every shadow-mode fill; a periodic task (or the
order service itself) calls `snapshot_and_push()` to send the latest
metrics to the strategy-registry.

The ledger is persisted via the same SqlStore the rest of the platform uses,
so a service restart doesn't lose history. (Falls back to in-memory only if
the SQL store cannot be reached — useful for tests.)
"""
from __future__ import annotations

import json
import logging
import math
import os
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable

UTC = timezone.utc
logger = logging.getLogger(__name__)


@dataclass
class ShadowFill:
    strategy_id: str
    user_id: str
    asset: str
    side: str                # BUY | SELL
    quantity: float
    entry_price: float
    exit_price: float | None = None
    pnl: float | None = None        # realized only
    realized: bool = False
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_row(self) -> dict:
        return {
            "strategy_id": self.strategy_id,
            "user_id": self.user_id,
            "asset": self.asset,
            "side": self.side,
            "quantity": float(self.quantity),
            "entry_price": float(self.entry_price),
            "exit_price": float(self.exit_price) if self.exit_price is not None else None,
            "pnl": float(self.pnl) if self.pnl is not None else None,
            "realized": bool(self.realized),
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class ShadowSnapshot:
    strategy_id: str
    pnl: float
    trade_count: int
    sharpe: float
    max_drawdown: float
    win_rate: float

    def to_payload(self) -> dict:
        # Matches strategy-registry's ShadowMetricsUpdate model
        return {
            "pnl": round(self.pnl, 6),
            "trade_count": int(self.trade_count),
            "sharpe": round(self.sharpe, 4),
            "max_drawdown": round(self.max_drawdown, 4),
            "win_rate": round(self.win_rate, 4),
        }


class ShadowRecorder:
    """Per-strategy rolling ledger of shadow fills + metric computation."""

    def __init__(
        self,
        registry_base_url: str | None = None,
        sql_url: str | None = None,
        rolling_window: int = 500,
    ) -> None:
        self._lock = threading.Lock()
        self._fills: dict[str, list[ShadowFill]] = defaultdict(list)
        self._last_snapshot: dict[str, ShadowSnapshot] = {}
        self._rolling_window = rolling_window
        self._registry_base = registry_base_url or os.getenv(
            "STRATEGY_REGISTRY_BASE_URL", "http://localhost:8005"
        )
        self._sql = None
        try:
            from shared.persistence import SqlStore
            self._sql = SqlStore(
                sql_url
                or os.getenv(
                    "POSTGRES_URL",
                    "postgresql+psycopg://postgres:postgres@localhost:5432/platform",
                )
            )
            self._ensure_schema()
            self._reload_recent()
        except Exception as exc:
            logger.warning("shadow_recorder_sql_unavailable", extra={"error": str(exc)[:200]})
            self._sql = None

    # ----- persistence -----

    def _ensure_schema(self) -> None:
        if not self._sql:
            return
        self._sql.execute(
            """
            CREATE TABLE IF NOT EXISTS shadow_fills (
                id BIGSERIAL PRIMARY KEY,
                strategy_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                asset TEXT NOT NULL,
                side TEXT NOT NULL,
                quantity DOUBLE PRECISION NOT NULL,
                entry_price DOUBLE PRECISION NOT NULL,
                exit_price DOUBLE PRECISION,
                pnl DOUBLE PRECISION,
                realized BOOLEAN NOT NULL DEFAULT FALSE,
                ts TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        self._sql.execute(
            "CREATE INDEX IF NOT EXISTS shadow_fills_strategy_ts ON shadow_fills(strategy_id, ts)"
        )

    def _reload_recent(self) -> None:
        if not self._sql:
            return
        try:
            rows = self._sql.fetch_all(
                """
                SELECT * FROM shadow_fills
                WHERE ts > NOW() - INTERVAL '60 days'
                ORDER BY ts ASC
                """
            )
            for r in rows or []:
                fill = ShadowFill(
                    strategy_id=r["strategy_id"],
                    user_id=r["user_id"],
                    asset=r["asset"],
                    side=r["side"],
                    quantity=float(r["quantity"]),
                    entry_price=float(r["entry_price"]),
                    exit_price=float(r["exit_price"]) if r.get("exit_price") is not None else None,
                    pnl=float(r["pnl"]) if r.get("pnl") is not None else None,
                    realized=bool(r["realized"]),
                    timestamp=r["ts"] if isinstance(r["ts"], datetime) else datetime.now(UTC),
                )
                self._fills[fill.strategy_id].append(fill)
        except Exception as exc:
            logger.warning("shadow_recorder_reload_failed", extra={"error": str(exc)[:200]})

    def _persist(self, fill: ShadowFill) -> None:
        if not self._sql:
            return
        try:
            self._sql.execute(
                """
                INSERT INTO shadow_fills
                  (strategy_id, user_id, asset, side, quantity, entry_price,
                   exit_price, pnl, realized, ts)
                VALUES
                  (:strategy_id, :user_id, :asset, :side, :quantity, :entry_price,
                   :exit_price, :pnl, :realized, :ts)
                """,
                {**fill.to_row(), "ts": fill.timestamp},
            )
        except Exception as exc:
            logger.warning("shadow_recorder_persist_failed", extra={"error": str(exc)[:200]})

    # ----- public API -----

    def record_fill(self, fill: ShadowFill) -> None:
        # D17: derive realized PnL from prior opposite-side fill (the caller
        # used to set pnl=0 because it couldn't see prior positions). Closes
        # the loop so MAB outcome update has a real reward signal instead of
        # 0 forever.
        if fill.realized and (fill.pnl is None or fill.pnl == 0.0):
            self._compute_pnl_from_prior_fill(fill)

        with self._lock:
            ledger = self._fills[fill.strategy_id]
            ledger.append(fill)
            # Bound memory: keep last N
            if len(ledger) > self._rolling_window * 2:
                self._fills[fill.strategy_id] = ledger[-self._rolling_window :]
        self._persist(fill)

    def _compute_pnl_from_prior_fill(self, fill: ShadowFill) -> None:
        """Find most recent opposite-side fill for same (strategy, user, asset)
        within a sliding window and compute realized pnl from it.

        Sign convention:
          BUY closes a short  → pnl = (entry - exit) * qty   (entry_short > exit_buy = profit)
          SELL closes a long  → pnl = (exit - entry) * qty   (exit_sell > entry_long = profit)

        If no opposite prior fill is found, the fill is treated as an opening
        leg and pnl stays at 0.
        """
        if not self._sql:
            return
        opposite = "SELL" if fill.side.upper() == "BUY" else "BUY"
        try:
            row = self._sql.fetch_one(
                """
                SELECT entry_price, quantity
                FROM shadow_fills
                WHERE strategy_id = :strategy_id
                  AND user_id = :user_id
                  AND asset = :asset
                  AND side = :side
                  AND ts > NOW() - INTERVAL '24 hours'
                ORDER BY ts DESC
                LIMIT 1
                """,
                {
                    "strategy_id": fill.strategy_id,
                    "user_id": fill.user_id,
                    "asset": fill.asset,
                    "side": opposite,
                },
            )
            if row is None:
                return  # opening leg — pnl stays at 0
            prior_entry = float(row["entry_price"])
            prior_qty = float(row["quantity"])
            closed_qty = min(fill.quantity, prior_qty)
            current_price = fill.entry_price  # caller writes fill_price into entry_price
            if fill.side.upper() == "SELL":
                pnl = (current_price - prior_entry) * closed_qty
            else:  # BUY closes a short
                pnl = (prior_entry - current_price) * closed_qty
            fill.exit_price = current_price
            fill.pnl = float(pnl)
        except Exception as exc:
            logger.warning("compute_pnl_failed", extra={"error": str(exc)[:200]})

    def snapshot(self, strategy_id: str) -> ShadowSnapshot | None:
        with self._lock:
            ledger = list(self._fills.get(strategy_id, []))
        realized = [f for f in ledger if f.realized and f.pnl is not None]
        if not realized:
            return None
        # Use the last `rolling_window` realized trades
        realized = realized[-self._rolling_window :]
        pnls = [f.pnl for f in realized]
        n = len(pnls)
        total_pnl = float(sum(pnls))
        wins = sum(1 for p in pnls if p > 0)
        win_rate = wins / n if n else 0.0

        # Sharpe on per-trade returns; annualize roughly to "many trades / year".
        # We don't know the exact frequency, so use a conservative √n scaling.
        if n >= 5:
            mean_pnl = total_pnl / n
            var = sum((p - mean_pnl) ** 2 for p in pnls) / max(n - 1, 1)
            std = math.sqrt(var)
            if std > 1e-12:
                sharpe = mean_pnl / std * math.sqrt(min(n, 365))
            else:
                sharpe = 0.0
        else:
            sharpe = 0.0

        # Max drawdown of cumulative PnL
        cum = 0.0
        peak = 0.0
        max_dd = 0.0
        for p in pnls:
            cum += p
            peak = max(peak, cum)
            dd = peak - cum
            if dd > max_dd:
                max_dd = dd
        # Express as fraction of peak (or |trough| if peak==0)
        max_dd_frac = max_dd / peak if peak > 1e-9 else (max_dd / max(abs(min(0.0, cum)), 1e-9))
        max_dd_frac = min(max_dd_frac, 1.0)

        snap = ShadowSnapshot(
            strategy_id=strategy_id,
            pnl=total_pnl,
            trade_count=n,
            sharpe=float(sharpe),
            max_drawdown=float(max_dd_frac),
            win_rate=float(win_rate),
        )
        with self._lock:
            self._last_snapshot[strategy_id] = snap
        return snap

    def push_snapshot(self, strategy_id: str) -> bool:
        snap = self.snapshot(strategy_id)
        if snap is None:
            return False
        try:
            import httpx
            url = f"{self._registry_base}/strategies/{strategy_id}/shadow/metrics"
            resp = httpx.post(url, json=snap.to_payload(), timeout=5.0)
            return resp.status_code == 200
        except Exception as exc:
            logger.warning(
                "shadow_recorder_push_failed",
                extra={"strategy_id": strategy_id, "error": str(exc)[:200]},
            )
            return False

    def push_all(self) -> dict[str, bool]:
        with self._lock:
            ids = list(self._fills.keys())
        return {sid: self.push_snapshot(sid) for sid in ids}

    def list_strategies(self) -> list[str]:
        with self._lock:
            return list(self._fills.keys())


# Module-level singleton — services import this so all calls go to one ledger
_recorder_singleton: ShadowRecorder | None = None


def get_recorder() -> ShadowRecorder:
    global _recorder_singleton
    if _recorder_singleton is None:
        _recorder_singleton = ShadowRecorder()
    return _recorder_singleton
