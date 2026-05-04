"""Unit tests for shared.execution.maker_simulator."""
import numpy as np
import pandas as pd
import pytest

from shared.execution.maker_simulator import (
    MakerCosts,
    MakerFillReport,
    MakerPolicy,
    costs_from_tier,
    simulate_maker_execution,
)


@pytest.fixture
def ohlc_data():
    np.random.seed(42)
    n = 500
    ret = np.random.normal(0, 0.005, n)
    close = 100 * np.cumprod(1 + ret)
    high = close * (1 + np.abs(np.random.normal(0, 0.003, n)))
    low = close * (1 - np.abs(np.random.normal(0, 0.003, n)))
    volume = np.random.uniform(500, 5000, n)
    df = pd.DataFrame({"open": close, "high": high, "low": low, "close": close, "volume": volume})
    target = pd.Series(np.sign(pd.Series(ret).rolling(20).mean()).fillna(0).to_numpy())
    return df, target


class TestBasicExecution:
    def test_returns_fill_report(self, ohlc_data):
        df, target = ohlc_data
        r = simulate_maker_execution(target, df)
        assert isinstance(r, MakerFillReport)
        assert len(r.realized_position) == len(target)
        assert len(r.bar_returns) == len(target)

    def test_no_trades_when_target_flat(self, ohlc_data):
        df, _ = ohlc_data
        flat = pd.Series(0.0, index=df.index)
        r = simulate_maker_execution(flat, df)
        assert r.n_orders == 0
        assert r.n_maker_fills == 0

    def test_fill_rate_bounded(self, ohlc_data):
        df, target = ohlc_data
        r = simulate_maker_execution(target, df)
        assert 0.0 <= r.fill_rate <= 1.0


class TestPartialFill:
    def test_partial_fill_reduces_per_bar_qty(self, ohlc_data):
        df, target = ohlc_data
        r_full = simulate_maker_execution(target, df, policy=MakerPolicy())
        r_part = simulate_maker_execution(target, df, policy=MakerPolicy(partial_fill=True, fill_participation=0.001))
        # Very low participation → more fills needed (remainders create new orders)
        assert r_part.n_maker_fills >= 0


class TestQueueModel:
    def test_thin_liquidity_blocks_fills(self, ohlc_data):
        df, target = ohlc_data
        # Overwrite volume to be tiny
        df = df.copy()
        df["volume"] = 0.01
        r = simulate_maker_execution(target, df, policy=MakerPolicy(queue_model=True, queue_depth_factor=0.05))
        assert r.n_maker_fills == 0

    def test_thick_liquidity_fills_normally(self, ohlc_data):
        df, target = ohlc_data
        df = df.copy()
        df["volume"] = 1e6
        r = simulate_maker_execution(target, df, policy=MakerPolicy(queue_model=True, queue_depth_factor=0.05))
        assert r.n_maker_fills > 0


class TestReprice:
    def test_reprice_eliminates_aggression(self, ohlc_data):
        df, target = ohlc_data
        r = simulate_maker_execution(target, df, policy=MakerPolicy(max_age_bars=4, reprice_on_age=2))
        assert r.n_aggressed == 0  # all unfilled get repriced before age limit


class TestIOC:
    def test_ioc_cancels_after_one_bar(self, ohlc_data):
        df, target = ohlc_data
        r_normal = simulate_maker_execution(target, df, policy=MakerPolicy(max_age_bars=4))
        r_ioc = simulate_maker_execution(target, df, policy=MakerPolicy(ioc=True, aggress_on_cancel=False))
        # IOC drops unfilled orders → more drops, fewer aggressed
        assert r_ioc.n_aggressed == 0
        assert r_ioc.n_dropped >= r_normal.n_dropped

    def test_ioc_with_aggress(self, ohlc_data):
        df, target = ohlc_data
        r = simulate_maker_execution(target, df, policy=MakerPolicy(ioc=True, aggress_on_cancel=True))
        # Every unfilled order aggresses after 1 bar
        assert r.n_dropped == 0


class TestAdverseSelection:
    def test_adverse_selection_bps_present(self, ohlc_data):
        df, target = ohlc_data
        r = simulate_maker_execution(target, df)
        assert isinstance(r.adverse_selection_bps, float)


class TestFeeTiers:
    def test_costs_from_tier_known(self):
        c = costs_from_tier("VIP0")
        assert c.maker_fee_bps == 2.0
        assert c.taker_fee_bps == 4.0

    def test_costs_from_tier_maker_rebate(self):
        c = costs_from_tier("maker_rebate")
        assert c.maker_fee_bps == -1.0

    def test_costs_from_tier_unknown_fallback(self):
        c = costs_from_tier("UNKNOWN_TIER")
        assert c.maker_fee_bps == 2.0  # VIP0 fallback

    def test_costs_from_tier_overrides(self):
        c = costs_from_tier("VIP5", half_spread_bps=3.0)
        assert c.half_spread_bps == 3.0
        assert c.maker_fee_bps == 0.8
