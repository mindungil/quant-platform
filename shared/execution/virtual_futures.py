"""Virtual Binance Futures connector — in-memory simulator.

Implements the same `ExchangeConnector` interface as BinanceFuturesConnector,
so any code (bridge, position tracker, order executor) works unchanged
against either. The only difference: all state is in-memory + persisted
to `data/virtual/`, and NO exchange API is called for authenticated
actions. Mark prices are fetched from Binance's **public** (no-auth) API.

## Isolation contract

This module must NEVER write to any of:
  - data/paper/*        (paper portfolio = separate tool)
  - data/logs/execution/* (reserved for testnet/live bridge)
  - .env or credentials store

All state lives under `data/virtual/`, marked by a tripwire file
`IS_VIRTUAL_NOT_REAL.txt`. The constructor refuses to accept a
`state_file` path outside this directory.

## Fill model (V1 = this file)

- MARKET: immediate fill at current Binance mark, taker fee.
- LIMIT: immediate fill — taker if the limit price crosses mark, maker
  otherwise. This is a V1 simplification; Phase V3 will wire the
  maker_simulator in for bar-level queued fills.

## Fee model

Default VIP0: 2 bps maker / 5 bps taker (2026 Binance Futures schedule).
Override via constructor args to test different tiers.

## Symbol filters

Known filters hard-coded for the active v4.4 universe. Unknown symbols
fall back to a conservative default. Orders that violate minNotional or
round to zero qty by stepSize are rejected with a reason.
"""
from __future__ import annotations

import json
import time
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from shared.execution.connector import ExchangeConnector
from shared.execution.risk_limits import OrderResult

UTC = timezone.utc

# ────────────────────────────────────────────────────────────────────
# Hard isolation paths — do not change lightly
# ────────────────────────────────────────────────────────────────────
VIRTUAL_DATA_ROOT = Path(__file__).resolve().parents[2] / "data" / "virtual"
VIRTUAL_STATE_FILE = VIRTUAL_DATA_ROOT / "state.json"
VIRTUAL_HISTORY_FILE = VIRTUAL_DATA_ROOT / "history.jsonl"
VIRTUAL_MARKER_FILE = VIRTUAL_DATA_ROOT / "IS_VIRTUAL_NOT_REAL.txt"

BINANCE_PUBLIC_MARK_URL = "https://fapi.binance.com/fapi/v1/premiumIndex"

# Binance USDT-M Futures symbol filters (subset for v4.4 universe).
# Values sourced from /fapi/v1/exchangeInfo on 2026-04-24.
SYMBOL_FILTERS: dict[str, dict[str, float]] = {
    "BTCUSDT":  {"min_notional": 5.0, "step_size": 1e-3, "tick_size": 0.10},
    "ETHUSDT":  {"min_notional": 5.0, "step_size": 1e-3, "tick_size": 0.01},
    "BNBUSDT":  {"min_notional": 5.0, "step_size": 1e-2, "tick_size": 0.01},
    "SOLUSDT":  {"min_notional": 5.0, "step_size": 1.0,  "tick_size": 0.001},
    "XRPUSDT":  {"min_notional": 5.0, "step_size": 1.0,  "tick_size": 0.0001},
    "DOGEUSDT": {"min_notional": 5.0, "step_size": 1.0,  "tick_size": 0.00001},
    "LINKUSDT": {"min_notional": 5.0, "step_size": 1e-2, "tick_size": 0.001},
}
DEFAULT_FILTER: dict[str, float] = {"min_notional": 5.0, "step_size": 1e-3, "tick_size": 0.01}

# VIP0 Binance Futures fees
DEFAULT_MAKER_BPS = 2.0
DEFAULT_TAKER_BPS = 5.0

_TRIPWIRE_SUBSTRING = "/virtual/"


@dataclass
class RealismConfig:
    """Toggles for execution realism. Defaults = off (backward compatible).

    When enabled, the virtual sim models:
      - **MARKET slippage**: price moves against you proportional to order
        notional (linear impact up to `slippage_max_bps`).
      - **LIMIT queue**: limit orders don't fill on the placing tick; they
        sit in `open_orders` and fill only when a subsequent mark-price
        refresh crosses the limit. If they never cross within
        `limit_ttl_seconds`, they expire.
      - **Partial fills**: a MARKET order larger than `partial_fill_threshold_pct`
        of equity is filled to `partial_fill_max_pct`, rest is rejected.
      - **Latency**: synthetic sleep on every call (for race-condition testing).
      - **Rate limit**: probability of a spurious REJECTED response.
    """
    slippage_enabled: bool = False
    slippage_bps_per_10k_usd: float = 0.5
    slippage_max_bps: float = 10.0

    limit_queue_enabled: bool = False
    limit_ttl_seconds: float = 3600 * 24  # expire 24h later
    # If > 0 and < 1: only this fraction of ticks cross the limit even
    # when price is on the right side (models queue position).
    limit_fill_prob: float = 1.0

    partial_fill_enabled: bool = False
    partial_fill_threshold_pct: float = 0.50  # "large" when notional/equity > 50%
    partial_fill_max_pct: float = 0.50        # fill at most 50% of requested qty

    latency_ms: float = 0.0
    rate_limit_fail_prob: float = 0.0


@dataclass
class VirtualState:
    equity: float
    balance: float                                   # free USDT
    positions: dict[str, float] = field(default_factory=dict)
    avg_entry_prices: dict[str, float] = field(default_factory=dict)
    open_orders: list[dict] = field(default_factory=list)
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    total_fees: float = 0.0
    n_orders: int = 0
    n_fills: int = 0
    n_rejected: int = 0
    initialized_at: str = ""
    last_update: str = ""


class VirtualFuturesConnector(ExchangeConnector):
    """In-memory Binance Futures simulator with full interface parity."""

    def __init__(
        self,
        initial_equity: float = 10_000.0,
        state_file: Path | str | None = None,
        history_file: Path | str | None = None,
        maker_bps: float = DEFAULT_MAKER_BPS,
        taker_bps: float = DEFAULT_TAKER_BPS,
        symbol_filters: dict | None = None,
        reset: bool = False,
        price_fetcher=None,
        realism: RealismConfig | None = None,
        rng=None,
    ) -> None:
        self.state_file = Path(state_file) if state_file else VIRTUAL_STATE_FILE
        self.history_file = Path(history_file) if history_file else VIRTUAL_HISTORY_FILE
        self.maker_bps = maker_bps
        self.taker_bps = taker_bps
        self.symbol_filters = symbol_filters if symbol_filters is not None else SYMBOL_FILTERS
        self._price_cache: dict[str, float] = {}
        self._price_fetcher = price_fetcher
        self.realism = realism or RealismConfig()
        # Inject a random source (tests can pass a seeded Random()). Default:
        # secrets/system RNG is overkill; use stdlib random with no seed so
        # results are non-reproducible unless explicitly seeded.
        import random as _random
        self._rng = rng if rng is not None else _random.Random()

        # ── Isolation tripwire ────────────────────────────────────────
        # Refuse to run if state file isn't under data/virtual/. This
        # prevents any accidental re-use of the connector against
        # paper/execution/real paths.
        state_str = str(self.state_file).replace("\\", "/")
        hist_str = str(self.history_file).replace("\\", "/")
        if _TRIPWIRE_SUBSTRING not in state_str:
            raise ValueError(
                f"VirtualFuturesConnector state_file must contain '{_TRIPWIRE_SUBSTRING}' "
                f"(got {self.state_file}). Refusing to proceed to protect non-virtual state."
            )
        if _TRIPWIRE_SUBSTRING not in hist_str:
            raise ValueError(
                f"VirtualFuturesConnector history_file must contain '{_TRIPWIRE_SUBSTRING}' "
                f"(got {self.history_file}). Refusing to proceed to protect non-virtual state."
            )

        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        marker = self.state_file.parent / "IS_VIRTUAL_NOT_REAL.txt"
        if not marker.exists():
            marker.write_text(
                "This directory contains VIRTUAL (simulated) trading state.\n"
                "NOT real money. NOT Binance testnet. NOT paper portfolio.\n"
                "Created by shared.execution.virtual_futures.VirtualFuturesConnector.\n"
            )

        if reset or not self.state_file.exists():
            self._state = VirtualState(
                equity=initial_equity,
                balance=initial_equity,
                initialized_at=datetime.now(UTC).isoformat(),
                last_update=datetime.now(UTC).isoformat(),
            )
            self._save_state()
        else:
            self._state = self._load_state()

    # ──────────────────────────────────────────────────────────────
    # Persistence
    # ──────────────────────────────────────────────────────────────
    def _load_state(self) -> VirtualState:
        with open(self.state_file) as f:
            raw = json.load(f)
        # Backward-compat: ignore extra keys
        fields = {f.name for f in VirtualState.__dataclass_fields__.values()}
        filtered = {k: v for k, v in raw.items() if k in fields}
        return VirtualState(**filtered)

    def _save_state(self) -> None:
        self._state.last_update = datetime.now(UTC).isoformat()
        tmp = self.state_file.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(asdict(self._state), f, indent=2, default=str)
        tmp.replace(self.state_file)

    def _append_history(self, record: dict) -> None:
        record.setdefault("timestamp", datetime.now(UTC).isoformat())
        with open(self.history_file, "a") as f:
            f.write(json.dumps(record, default=str) + "\n")

    # ──────────────────────────────────────────────────────────────
    # Symbol filter helpers
    # ──────────────────────────────────────────────────────────────
    def _filter(self, symbol: str) -> dict:
        return self.symbol_filters.get(symbol, DEFAULT_FILTER)

    def _round_quantity(self, symbol: str, qty: float) -> float:
        step = self._filter(symbol)["step_size"]
        if step <= 0:
            return qty
        return round(qty / step) * step

    def _round_price(self, symbol: str, price: float) -> float:
        tick = self._filter(symbol)["tick_size"]
        if tick <= 0:
            return price
        return round(price / tick) * tick

    # ──────────────────────────────────────────────────────────────
    # ExchangeConnector — public interface
    # ──────────────────────────────────────────────────────────────
    def _fetch_mark_prices_raw(self, symbols: list[str]) -> dict[str, float]:
        """Fetch prices without running the open-order processor — used
        internally to avoid recursion from process_open_orders()."""
        if self._price_fetcher is not None:
            prices = self._price_fetcher(symbols) or {}
            self._price_cache.update(prices)
            return dict(prices)
        try:
            req = urllib.request.Request(
                BINANCE_PUBLIC_MARK_URL,
                headers={"User-Agent": "quant-virtual/1.0"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
            prices = {}
            wanted = set(symbols)
            for item in data:
                if item["symbol"] in wanted:
                    prices[item["symbol"]] = float(item["markPrice"])
            self._price_cache.update(prices)
            return prices
        except Exception:
            return {s: self._price_cache[s] for s in symbols if s in self._price_cache}

    def get_mark_prices(self, symbols: list[str]) -> dict[str, float]:
        self._maybe_sleep()
        prices = self._fetch_mark_prices_raw(symbols)
        # Each mark-price refresh is a "tick" — process any queued limit
        # orders against the new prices.
        if self.realism.limit_queue_enabled and self._state.open_orders:
            self.process_open_orders()
        return prices

    def get_positions(self) -> dict[str, float]:
        return {s: q for s, q in self._state.positions.items() if abs(q) > 1e-10}

    def get_balances(self) -> dict[str, float]:
        return {"USDT": float(self._state.balance)}

    def get_account_equity(self) -> float:
        self._recompute_unrealized()
        return float(self._state.equity)

    def place_market_order(self, symbol: str, side: str, quantity: float) -> OrderResult:
        return self._execute_order(symbol, side, quantity, order_type="MARKET", price=None)

    def place_limit_order(self, symbol: str, side: str, quantity: float, price: float) -> OrderResult:
        return self._execute_order(symbol, side, quantity, order_type="LIMIT", price=price)

    def cancel_order(self, symbol: str, order_id: str) -> bool:
        before = len(self._state.open_orders)
        self._state.open_orders = [o for o in self._state.open_orders if o.get("order_id") != order_id]
        if len(self._state.open_orders) < before:
            self._save_state()
            return True
        return False

    # ──────────────────────────────────────────────────────────────
    # Order execution pipeline (V1 = immediate fill at mark)
    # ──────────────────────────────────────────────────────────────
    def _execute_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        *,
        order_type: str,
        price: float | None,
    ) -> OrderResult:
        self._maybe_sleep()
        self._state.n_orders += 1
        side = side.upper()

        # Simulated rate-limit rejection (random)
        if self._rate_limit_hit():
            return self._reject(symbol, side, quantity, "rate limit (simulated)")

        qty = self._round_quantity(symbol, abs(quantity))
        if qty <= 0:
            return self._reject(symbol, side, quantity, "stepSize rounds to zero")

        prices = self._fetch_mark_prices_raw([symbol])
        mark = prices.get(symbol)
        if mark is None or mark <= 0:
            return self._reject(symbol, side, qty, f"no mark price for {symbol}")

        # Partial fill: a very large MARKET order fills to max_pct and the
        # rest is rejected (opaque to the caller — they see filled_qty < qty).
        partial_split = None  # (fill_qty, reject_qty) or None
        if (order_type == "MARKET"
                and self.realism.partial_fill_enabled
                and self._state.equity > 0):
            notional_full = qty * mark
            if notional_full / self._state.equity > self.realism.partial_fill_threshold_pct:
                fill_qty = self._round_quantity(symbol, qty * self.realism.partial_fill_max_pct)
                reject_qty = qty - fill_qty
                if fill_qty > 0:
                    partial_split = (fill_qty, reject_qty)

        if partial_split is not None:
            qty = partial_split[0]

        # Fill-price + fee selection
        if order_type == "MARKET":
            fill_price = self._apply_market_slippage(side, qty, mark)
            fee_bps = self.taker_bps
            is_maker = False
        else:  # LIMIT
            if price is None or price <= 0:
                return self._reject(symbol, side, qty, "LIMIT requires positive price")
            price = self._round_price(symbol, price)
            crosses_now = (side == "BUY" and price >= mark) or (side == "SELL" and price <= mark)

            if self.realism.limit_queue_enabled and not crosses_now:
                # Park the order. Will fill on a later tick when mark crosses.
                return self._enqueue_limit(symbol, side, qty, price)

            if crosses_now:
                fill_price = mark
                fee_bps = self.taker_bps
                is_maker = False
            else:
                # V1 fallback: no queue → immediate maker fill at limit price.
                fill_price = price
                fee_bps = self.maker_bps
                is_maker = True

        notional = qty * fill_price
        min_notional = self._filter(symbol)["min_notional"]
        if notional < min_notional:
            return self._reject(
                symbol, side, qty,
                f"notional ${notional:.2f} < minNotional ${min_notional:.2f}",
            )

        result = self._settle_fill(
            symbol=symbol, side=side, qty=qty, fill_price=fill_price,
            fee_bps=fee_bps, is_maker=is_maker,
            order_type=order_type,
            limit_price=price if order_type == "LIMIT" else None,
        )

        if partial_split is not None:
            # Record the rejected remainder for observability
            _, reject_qty = partial_split
            self._append_history({
                "type": "partial_reject",
                "order_id": result.order_id,
                "symbol": symbol,
                "side": side,
                "rejected_qty": reject_qty,
                "reason": "partial fill (large order)",
            })

        return result

    def _reject(self, symbol: str, side: str, qty: float, reason: str) -> OrderResult:
        self._state.n_rejected += 1
        self._append_history({
            "type": "reject",
            "symbol": symbol,
            "side": side,
            "quantity": qty,
            "reason": reason,
        })
        self._save_state()
        return OrderResult(
            symbol=symbol, side=side, quantity=qty,
            filled_quantity=0.0, avg_price=0.0,
            status="REJECTED", error=reason,
        )

    # ──────────────────────────────────────────────────────────────
    # Realism helpers
    # ──────────────────────────────────────────────────────────────
    def _apply_market_slippage(self, side: str, qty: float, mark: float) -> float:
        r = self.realism
        if not r.slippage_enabled or mark <= 0:
            return mark
        notional = qty * mark
        bps = min(
            r.slippage_bps_per_10k_usd * (notional / 10_000.0),
            r.slippage_max_bps,
        )
        sign = 1 if side.upper() == "BUY" else -1
        return mark * (1 + sign * bps * 1e-4)

    def _rate_limit_hit(self) -> bool:
        p = self.realism.rate_limit_fail_prob
        if p <= 0:
            return False
        return self._rng.random() < p

    def _maybe_sleep(self) -> None:
        ms = self.realism.latency_ms
        if ms > 0:
            time.sleep(ms / 1000.0)

    def _enqueue_limit(self, symbol: str, side: str, qty: float, price: float) -> OrderResult:
        order_id = f"V-LIMIT-{int(time.time() * 1000)}-{len(self._state.open_orders) + 1}"
        entry = {
            "order_id": order_id,
            "symbol": symbol,
            "side": side,
            "quantity": qty,
            "price": price,
            "placed_at": time.time(),
            "status": "NEW",
        }
        self._state.open_orders.append(entry)
        self._append_history({"type": "place_limit", **entry})
        self._save_state()
        return OrderResult(
            symbol=symbol, side=side, quantity=qty,
            filled_quantity=0.0, avg_price=0.0,
            status="NEW", order_id=order_id,
        )

    def process_open_orders(self) -> list[OrderResult]:
        """Check resting LIMIT orders against latest mark; fill or expire.

        Called automatically by get_mark_prices() when `limit_queue_enabled`,
        so external callers typically don't invoke it — but bridge/tests can.
        """
        if not self._state.open_orders:
            return []
        symbols = list({o["symbol"] for o in self._state.open_orders})
        prices = self._fetch_mark_prices_raw(symbols)
        now = time.time()
        remaining: list[dict] = []
        filled: list[OrderResult] = []
        for o in self._state.open_orders:
            mark = prices.get(o["symbol"])
            if mark is None:
                remaining.append(o)
                continue
            side = o["side"]
            limit = o["price"]
            crossed = (side == "BUY" and mark <= limit) or (side == "SELL" and mark >= limit)
            # Probabilistic queue position: even when crossed, only fill p of the time.
            if crossed and self._rng.random() <= self.realism.limit_fill_prob:
                res = self._fill_queued_order(o, limit)
                filled.append(res)
                continue
            age = now - o.get("placed_at", now)
            if age > self.realism.limit_ttl_seconds:
                self._append_history({"type": "expire", "order_id": o["order_id"], "age_s": age})
                continue
            remaining.append(o)
        self._state.open_orders = remaining
        self._save_state()
        return filled

    def _fill_queued_order(self, o: dict, fill_price: float) -> OrderResult:
        """Complete a queued LIMIT order — treat as maker fill at limit price."""
        symbol = o["symbol"]
        side = o["side"]
        qty = o["quantity"]
        result = self._settle_fill(
            symbol=symbol, side=side, qty=qty, fill_price=fill_price,
            fee_bps=self.maker_bps, is_maker=True,
            order_type="LIMIT_QUEUED", limit_price=o["price"],
            order_id=o["order_id"],
        )
        return result

    def _settle_fill(
        self, symbol: str, side: str, qty: float, fill_price: float,
        fee_bps: float, is_maker: bool, order_type: str,
        limit_price: float | None = None, order_id: str | None = None,
    ) -> OrderResult:
        """Shared accounting for any fill (MARKET, LIMIT immediate, LIMIT queued)."""
        signed_qty = qty if side == "BUY" else -qty
        old_pos = self._state.positions.get(symbol, 0.0)
        new_pos = old_pos + signed_qty
        old_avg = self._state.avg_entry_prices.get(symbol, 0.0)

        realized = 0.0
        if old_pos != 0 and ((old_pos > 0) != (signed_qty > 0)):
            closing_qty = min(abs(signed_qty), abs(old_pos))
            direction = 1 if old_pos > 0 else -1
            realized = direction * closing_qty * (fill_price - old_avg)

        if abs(new_pos) < 1e-12:
            self._state.avg_entry_prices.pop(symbol, None)
        elif old_pos == 0 or (new_pos > 0) != (old_pos > 0):
            self._state.avg_entry_prices[symbol] = fill_price
        elif (signed_qty > 0) == (old_pos > 0):
            total_qty = abs(old_pos) + abs(signed_qty)
            old_notional = abs(old_pos) * old_avg
            add_notional = abs(signed_qty) * fill_price
            self._state.avg_entry_prices[symbol] = (old_notional + add_notional) / total_qty

        notional = qty * fill_price
        fee = notional * fee_bps * 1e-4
        self._state.positions[symbol] = new_pos
        self._state.balance += realized - fee
        self._state.realized_pnl += realized
        self._state.total_fees += fee
        self._state.n_fills += 1
        self._recompute_unrealized()

        oid = order_id or f"V-{int(time.time() * 1000)}-{self._state.n_fills}"
        self._append_history({
            "type": "fill",
            "order_id": oid,
            "symbol": symbol,
            "side": side,
            "order_type": order_type,
            "filled_qty": qty,
            "limit_price": limit_price,
            "fill_price": fill_price,
            "fee": fee,
            "fee_bps": fee_bps,
            "is_maker": is_maker,
            "realized_pnl": realized,
            "new_position": new_pos,
            "balance_after": self._state.balance,
            "equity_after": self._state.equity,
        })
        self._save_state()
        return OrderResult(
            symbol=symbol, side=side,
            quantity=qty, filled_quantity=qty,
            avg_price=fill_price, status="FILLED", order_id=oid,
        )

    def _recompute_unrealized(self) -> None:
        positions = {s: q for s, q in self._state.positions.items() if abs(q) > 1e-10}
        if not positions:
            self._state.unrealized_pnl = 0.0
            self._state.equity = self._state.balance
            return
        # Raw fetch — must not re-enter process_open_orders (recursion).
        prices = self._fetch_mark_prices_raw(list(positions.keys()))
        upl = 0.0
        for sym, q in positions.items():
            mark = prices.get(sym) or self._state.avg_entry_prices.get(sym, 0.0)
            avg = self._state.avg_entry_prices.get(sym, mark)
            upl += q * (mark - avg)  # long q>0 profits when mark>avg; short q<0 profits when mark<avg
        self._state.unrealized_pnl = upl
        self._state.equity = self._state.balance + upl

    # ──────────────────────────────────────────────────────────────
    # Inspection / maintenance
    # ──────────────────────────────────────────────────────────────
    def snapshot(self) -> dict:
        """Return a dict snapshot of current state (for CLI/tests)."""
        self._recompute_unrealized()
        return asdict(self._state)

    def reset_state(self, initial_equity: float | None = None) -> None:
        eq = initial_equity if initial_equity is not None else self._state.equity
        self._state = VirtualState(
            equity=eq,
            balance=eq,
            initialized_at=datetime.now(UTC).isoformat(),
            last_update=datetime.now(UTC).isoformat(),
        )
        self._save_state()
        self._append_history({"type": "reset", "new_equity": eq})
