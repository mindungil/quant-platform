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
        # F2: FIFO open-leg queue per (strategy_id, user_id, asset).
        # Each entry: {"side": "BUY"|"SELL", "qty": float, "entry_price": float}.
        # Used to correctly pair consecutive fills: the previous logic
        # (most-recent-opposite SELECT … LIMIT 1) re-paired against the same
        # stale leg on runs of same-side fills, producing phantom pnl.
        self._open_legs: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
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
            # F2: replay loaded fills through the FIFO matcher to rebuild the
            # open-leg queue so subsequent new fills pair against the right
            # historical positions (rather than starting from an empty book).
            self._rebuild_open_legs()
        except Exception as exc:
            logger.warning("shadow_recorder_reload_failed", extra={"error": str(exc)[:200]})

    def _rebuild_open_legs(self) -> None:
        """Replay all loaded fills in timestamp order to reconstruct FIFO state.

        Doesn't mutate stored pnl on disk — only updates self._open_legs so
        future calls to record_fill have the correct queue context.
        """
        self._open_legs.clear()
        all_fills: list[ShadowFill] = []
        for ledger in self._fills.values():
            all_fills.extend(ledger)
        all_fills.sort(key=lambda f: f.timestamp)
        for f in all_fills:
            if f.realized:
                key = (f.strategy_id, f.user_id, f.asset)
                self._fifo_match(f.side, f.quantity, f.entry_price, key)

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
        # F2: always run the FIFO matcher for realized fills so the open-leg
        # queue stays consistent with the actual fill sequence. Only overwrite
        # pnl when the caller didn't compute one — preserves upstream PnL but
        # keeps the queue authoritative for future pairings.
        if fill.realized:
            had_pnl = fill.pnl not in (None, 0.0)
            key = (fill.strategy_id, fill.user_id, fill.asset)
            with self._lock:
                matched_qty, pnl = self._fifo_match(
                    fill.side, fill.quantity, fill.entry_price, key
                )
            if matched_qty > 0 and not had_pnl:
                fill.exit_price = fill.entry_price
                fill.pnl = float(pnl)

        with self._lock:
            ledger = self._fills[fill.strategy_id]
            ledger.append(fill)
            # Bound memory: keep last N
            if len(ledger) > self._rolling_window * 2:
                self._fills[fill.strategy_id] = ledger[-self._rolling_window :]
        self._persist(fill)

    def _fifo_match(
        self, side: str, qty: float, price: float, key: tuple[str, str, str]
    ) -> tuple[float, float]:
        """FIFO-match an incoming fill against opposite-side open legs.

        Mutates self._open_legs[key]: drains matched legs from the front and
        appends a new leg on the same side when qty exceeds the matched book.
        Returns (matched_qty, realized_pnl).

        Sign convention (price = incoming fill price):
          SELL closes a long  → pnl = (price - entry) * qty
          BUY  closes a short → pnl = (entry - price) * qty
        """
        queue = self._open_legs[key]
        incoming_qty = float(qty)
        incoming_side = side.upper()
        pnl = 0.0
        matched_qty = 0.0
        EPS = 1e-12
        while incoming_qty > EPS and queue and queue[0]["side"] != incoming_side:
            leg = queue[0]
            take = min(incoming_qty, leg["qty"])
            if incoming_side == "SELL":
                pnl += (price - leg["entry_price"]) * take
            else:  # BUY closes a short
                pnl += (leg["entry_price"] - price) * take
            leg["qty"] -= take
            incoming_qty -= take
            matched_qty += take
            if leg["qty"] < EPS:
                queue.pop(0)
        if incoming_qty > EPS:
            queue.append(
                {"side": incoming_side, "qty": incoming_qty, "entry_price": float(price)}
            )
        return matched_qty, pnl

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
