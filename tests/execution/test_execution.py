"""Tests for execution module — risk limits, position tracker, order executor."""
import pytest
from shared.execution.risk_limits import (
    RiskLimits, TradeOrder, OrderResult, check_pre_trade,
)
from shared.execution.connector import ExchangeConnector
from shared.execution.position_tracker import PositionTracker
from shared.execution.order_executor import OrderExecutor


# ----- Mock Connector -----

class MockConnector(ExchangeConnector):
    def __init__(self, positions=None, balances=None, equity=10000, prices=None):
        self._positions = positions or {}
        self._balances = balances or {"USDT": 10000}
        self._equity = equity
        self._prices = prices or {}
        self.orders_placed = []

    def get_positions(self): return dict(self._positions)
    def get_balances(self): return dict(self._balances)
    def get_account_equity(self): return self._equity
    def get_mark_prices(self, symbols):
        return {s: self._prices.get(s, 100) for s in symbols}

    def place_market_order(self, symbol, side, quantity):
        self.orders_placed.append((symbol, side, quantity, "MARKET"))
        return OrderResult(symbol=symbol, side=side, quantity=quantity,
                           filled_quantity=quantity, avg_price=100,
                           status="FILLED", order_id="mock-123")

    def place_limit_order(self, symbol, side, quantity, price):
        self.orders_placed.append((symbol, side, quantity, "LIMIT"))
        return OrderResult(symbol=symbol, side=side, quantity=quantity,
                           filled_quantity=quantity, avg_price=price,
                           status="FILLED", order_id="mock-456")

    def cancel_order(self, symbol, order_id): return True


# ----- Risk Limits Tests -----

class TestRiskLimits:

    def test_dd_halt(self):
        order = TradeOrder(symbol="BTCUSDT", side="BUY", quantity=1, price=100)
        limits = RiskLimits(max_drawdown_halt=0.15)
        passed, reason = check_pre_trade(order, limits, 10000, {}, 0, 0.20)
        assert not passed
        assert "Drawdown halt" in reason

    def test_daily_turnover_limit(self):
        order = TradeOrder(symbol="BTCUSDT", side="BUY", quantity=1, price=100)
        limits = RiskLimits(max_daily_turnover=2.0)
        passed, _ = check_pre_trade(order, limits, 10000, {}, 2.5, 0)
        assert not passed

    def test_order_too_large(self):
        order = TradeOrder(symbol="BTCUSDT", side="BUY", quantity=10, price=1000)
        limits = RiskLimits(max_single_order_notional=0.05)
        # 10 * 1000 = 10000, equity = 10000, ratio = 1.0 > 0.05
        passed, _ = check_pre_trade(order, limits, 10000, {}, 0, 0)
        assert not passed

    def test_order_too_small(self):
        order = TradeOrder(symbol="BTCUSDT", side="BUY", quantity=0.001, price=5)
        limits = RiskLimits(min_order_size_usd=10)
        passed, _ = check_pre_trade(order, limits, 10000, {}, 0, 0)
        assert not passed

    def test_normal_order_passes(self):
        order = TradeOrder(symbol="BTCUSDT", side="BUY", quantity=0.1, price=500)
        limits = RiskLimits()
        passed, reason = check_pre_trade(order, limits, 10000, {}, 0, 0)
        assert passed
        assert reason == "OK"

    def test_reduce_only_bypasses_position_limit(self):
        order = TradeOrder(symbol="BTCUSDT", side="SELL", quantity=1, price=100,
                           reduce_only=True)
        limits = RiskLimits(max_position_per_symbol=0.01)
        # Position is huge but reduce_only should pass
        positions = {"BTCUSDT": 5000}
        passed, _ = check_pre_trade(order, limits, 10000, positions, 0, 0)
        assert passed


# ----- Position Tracker Tests -----

class TestPositionTracker:

    def test_reconcile_new_position(self):
        conn = MockConnector(positions={})
        tracker = PositionTracker(conn, min_trade_notional=1)
        result = tracker.reconcile(
            {"BTCUSDT": 0.5}, {"BTCUSDT": 80000}
        )
        assert len(result.orders_needed) == 1
        assert result.orders_needed[0].side == "BUY"
        assert result.orders_needed[0].quantity == 0.5

    def test_reconcile_reduce_position(self):
        conn = MockConnector(positions={"BTCUSDT": 1.0})
        tracker = PositionTracker(conn, min_trade_notional=1)
        result = tracker.reconcile(
            {"BTCUSDT": 0.5}, {"BTCUSDT": 80000}
        )
        assert len(result.orders_needed) == 1
        assert result.orders_needed[0].side == "SELL"
        assert result.orders_needed[0].reduce_only

    def test_reconcile_close_position(self):
        conn = MockConnector(positions={"ETHUSDT": -2.0})
        tracker = PositionTracker(conn, min_trade_notional=1)
        result = tracker.reconcile(
            {}, {"ETHUSDT": 3000}
        )
        assert len(result.orders_needed) == 1
        assert result.orders_needed[0].side == "BUY"
        assert result.orders_needed[0].quantity == 2.0

    def test_reconcile_skip_small(self):
        conn = MockConnector(positions={"BTCUSDT": 1.0})
        tracker = PositionTracker(conn, min_trade_notional=100)
        result = tracker.reconcile(
            {"BTCUSDT": 1.001}, {"BTCUSDT": 80000}
        )
        # Delta = 0.001 * 80000 = $80 < $100 min
        assert len(result.orders_needed) == 0
        assert len(result.skipped) == 1

    def test_no_change_needed(self):
        conn = MockConnector(positions={"BTCUSDT": 0.5})
        tracker = PositionTracker(conn, min_trade_notional=1)
        result = tracker.reconcile(
            {"BTCUSDT": 0.5}, {"BTCUSDT": 80000}
        )
        assert len(result.orders_needed) == 0


# ----- Order Executor Tests -----

class TestOrderExecutor:

    def test_dry_run(self):
        conn = MockConnector()
        executor = OrderExecutor(conn, dry_run=True, log_dir="/tmp/test_exec_log")
        orders = [TradeOrder(symbol="BTCUSDT", side="BUY", quantity=0.005)]
        result = executor.execute(orders, equity=10000, current_positions={},
                                  prices={"BTCUSDT": 80000})  # 0.005 * 80000 = $400 = 4%
        assert result.orders_filled == 1
        assert result.results[0].status == "DRY_RUN"
        assert len(conn.orders_placed) == 0  # no real orders in dry-run

    def test_live_execution(self):
        conn = MockConnector()
        executor = OrderExecutor(conn, dry_run=False, log_dir="/tmp/test_exec_log")
        orders = [TradeOrder(symbol="BTCUSDT", side="BUY", quantity=0.1)]
        result = executor.execute(orders, equity=10000, current_positions={},
                                  prices={"BTCUSDT": 100})
        assert result.orders_filled == 1
        assert result.results[0].status == "FILLED"
        assert len(conn.orders_placed) == 1

    def test_risk_rejection(self):
        conn = MockConnector()
        limits = RiskLimits(max_drawdown_halt=0.10)
        executor = OrderExecutor(conn, risk_limits=limits, dry_run=False,
                                 log_dir="/tmp/test_exec_log")
        orders = [TradeOrder(symbol="BTCUSDT", side="BUY", quantity=0.1)]
        result = executor.execute(orders, equity=10000, current_positions={},
                                  current_drawdown=0.15,
                                  prices={"BTCUSDT": 100})
        assert result.orders_failed == 1
        assert result.results[0].status == "REJECTED"
        assert len(conn.orders_placed) == 0
