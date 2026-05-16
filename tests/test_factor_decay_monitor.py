"""Tests for shared.factors.decay_monitor — rolling IC + auto-deprecate.

IP test (touches factor-IP territory; the auto-deprecate threshold is a
tuning parameter we keep private). Added to ops/private_paths.txt with the
other IP-dependent tests.
"""
from __future__ import annotations

import numpy as np
import pytest

from shared.factors.decay_monitor import FactorDecayMonitor


def _record_correlated(
    monitor: FactorDecayMonitor,
    factor: str,
    n: int,
    correlation: float,
    seed: int = 0,
) -> None:
    """Feed n (score, forward_return) pairs with a target Pearson correlation."""
    rng = np.random.default_rng(seed)
    s = rng.normal(0, 1, n)
    noise = rng.normal(0, 1, n)
    # r = correlation * s + sqrt(1 - corr²) * noise → corr(s, r) ≈ correlation
    r = correlation * s + np.sqrt(max(1 - correlation ** 2, 0)) * noise
    for si, ri in zip(s, r):
        monitor.record(factor, float(si), float(ri))


# ──────────────────────────────────────────────────────────────────
# Construction / validation
# ──────────────────────────────────────────────────────────────────


def test_monitor_validates_constructor() -> None:
    with pytest.raises(ValueError):
        FactorDecayMonitor(ic_window=3)
    with pytest.raises(ValueError):
        FactorDecayMonitor(ir_window=2)
    with pytest.raises(ValueError):
        FactorDecayMonitor(ir_threshold=0)
    with pytest.raises(ValueError):
        FactorDecayMonitor(ir_threshold=10)


def test_monitor_auto_sets_min_observations() -> None:
    m = FactorDecayMonitor(ic_window=20, ir_window=50)
    assert m.min_observations == 70


def test_monitor_explicit_min_observations_preserved() -> None:
    m = FactorDecayMonitor(min_observations=500)
    assert m.min_observations == 500


# ──────────────────────────────────────────────────────────────────
# Recording / IC computation
# ──────────────────────────────────────────────────────────────────


def test_no_ic_during_warmup() -> None:
    m = FactorDecayMonitor(ic_window=30, ir_window=50)
    for _ in range(15):
        m.record("f1", 0.5, 0.001)
    assert m.current_ic("f1") is None


def test_ic_appears_after_window_full() -> None:
    m = FactorDecayMonitor(ic_window=20, ir_window=30)
    _record_correlated(m, "f1", n=25, correlation=0.7, seed=1)
    assert m.current_ic("f1") is not None
    # Should be close to the target correlation (loose tolerance — small n)
    assert m.current_ic("f1") > 0.4


def test_unknown_factor_returns_none() -> None:
    m = FactorDecayMonitor()
    assert m.current_ic("never_seen") is None
    assert m.current_ic_ir("never_seen") is None
    assert m.n_observations("never_seen") == 0


def test_constant_input_yields_no_ic() -> None:
    """Zero variance in either series → can't compute correlation."""
    m = FactorDecayMonitor(ic_window=20, ir_window=30)
    for _ in range(40):
        m.record("flat", 0.5, 0.001)  # both constant
    assert m.current_ic("flat") is None


# ──────────────────────────────────────────────────────────────────
# IC_IR
# ──────────────────────────────────────────────────────────────────


def test_strong_persistent_signal_yields_high_ic_ir() -> None:
    """Steadily-correlated factor → high mean IC, low variance → IR >> threshold."""
    # Smaller windows so the test runs quickly while still producing ≥ ir_window
    # disjoint IC samples (1000 records / 20 = 50 IC values).
    m = FactorDecayMonitor(ic_window=20, ir_window=30, ir_threshold=0.2)
    _record_correlated(m, "winner", n=1000, correlation=0.6, seed=42)
    ir = m.current_ic_ir("winner")
    assert ir is not None
    assert abs(ir) > 0.5
    assert not m.is_decayed("winner")
    assert m.active_weight("winner") == 1.0


def test_noise_yields_low_ic_ir_and_flags_decayed() -> None:
    """Random noise → IC ≈ 0 across disjoint windows → IR near zero → decayed."""
    m = FactorDecayMonitor(ic_window=20, ir_window=30, ir_threshold=0.5)
    _record_correlated(m, "noise", n=1000, correlation=0.0, seed=11)
    ir = m.current_ic_ir("noise")
    assert ir is not None
    assert abs(ir) < 0.5  # noise → low IR
    assert m.is_decayed("noise")
    assert m.active_weight("noise") == 0.0


def test_warmup_factor_never_flagged_decayed() -> None:
    """Below min_observations → never decayed regardless of IC."""
    m = FactorDecayMonitor(ic_window=10, ir_window=10, min_observations=300)
    _record_correlated(m, "noise", n=50, correlation=0.0, seed=2)
    assert not m.is_decayed("noise")
    assert m.active_weight("noise") == 1.0


# ──────────────────────────────────────────────────────────────────
# Recovery semantics
# ──────────────────────────────────────────────────────────────────


def test_factor_can_recover() -> None:
    """A factor flagged decayed should re-activate once IC_IR rebounds."""
    m = FactorDecayMonitor(ic_window=20, ir_window=30, ir_threshold=0.4)
    # Phase 1: noise for 1000 records → 50 IC samples → decayed
    _record_correlated(m, "f", n=1000, correlation=0.0, seed=7)
    assert m.is_decayed("f")
    # Phase 2: strong signal pushes IC_IR back up (deque sliding evicts noise IC)
    _record_correlated(m, "f", n=1000, correlation=0.7, seed=8)
    ir2 = m.current_ic_ir("f")
    assert ir2 is not None and abs(ir2) > 0.4
    assert not m.is_decayed("f")


# ──────────────────────────────────────────────────────────────────
# Diagnostics
# ──────────────────────────────────────────────────────────────────


def test_status_dict_shape() -> None:
    m = FactorDecayMonitor(ic_window=20, ir_window=30)
    _record_correlated(m, "f", n=100, correlation=0.5, seed=3)
    s = m.status("f")
    assert set(s.keys()) == {
        "factor", "n_obs", "current_ic", "ic_ir", "is_decayed", "active_weight",
    }
    assert s["factor"] == "f"
    assert s["n_obs"] > 0


def test_all_status_covers_every_tracked_factor() -> None:
    m = FactorDecayMonitor(ic_window=10, ir_window=10)
    for n in ("a", "b", "c"):
        _record_correlated(m, n, 30, 0.3, seed=hash(n) % 100)
    out = m.all_status()
    assert set(out.keys()) == {"a", "b", "c"}


# ──────────────────────────────────────────────────────────────────
# Pearson alternative
# ──────────────────────────────────────────────────────────────────


def test_pearson_alternative_works() -> None:
    m = FactorDecayMonitor(ic_window=20, ir_window=30, use_spearman=False)
    _record_correlated(m, "f", n=100, correlation=0.5, seed=4)
    assert m.current_ic("f") is not None
