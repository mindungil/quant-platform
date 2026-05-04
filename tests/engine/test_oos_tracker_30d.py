"""Unit tests for scripts/live/oos_tracker_30d.py band logic."""

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts/live/oos_tracker_30d.py"
spec = importlib.util.spec_from_file_location("oos_tracker_30d", SCRIPT)
ot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ot)  # type: ignore[union-attr]


def test_se_decreases_with_n():
    """Larger N → smaller standard error."""
    se_small = ot.sr_standard_error(0.5, 100)
    se_large = ot.sr_standard_error(0.5, 10000)
    assert se_large < se_small
    assert se_small > 0


def test_se_zero_or_one_returns_inf():
    """Insufficient sample → infinite SE (insufficient_data)."""
    assert ot.sr_standard_error(0.5, 0) == float("inf")
    assert ot.sr_standard_error(0.5, 1) == float("inf")


def test_in_band_status():
    """Live SR within ±k·SE of expected → in_band."""
    band = ot.compute_band(live_sr=0.40, expected_sr=0.40, n_obs=720, k=1.0)
    assert band["status"] == "in_band"
    assert band["z_score"] == 0.0


def test_below_band_status():
    """Live SR significantly under expected → below_band."""
    band = ot.compute_band(live_sr=-1.0, expected_sr=0.40, n_obs=720, k=1.0)
    assert band["status"] == "below_band"
    assert band["z_score"] < -1.0


def test_above_band_status():
    """Live SR significantly over expected → above_band."""
    band = ot.compute_band(live_sr=1.50, expected_sr=0.27, n_obs=720, k=1.0)
    assert band["status"] == "above_band"
    assert band["z_score"] > 1.0


def test_band_widens_with_higher_k():
    """k=2σ band must be wider than k=1σ; observation can flip status."""
    # An observation at exactly 1.5σ below expected
    band_1s = ot.compute_band(live_sr=0.30, expected_sr=0.40, n_obs=100, k=1.0)
    band_2s = ot.compute_band(live_sr=0.30, expected_sr=0.40, n_obs=100, k=2.0)
    # With k=1, lower band is closer to expected → live=0.30 likely below band
    # With k=2, lower band is farther → live=0.30 likely in band
    assert band_2s["lower_band_1sigma"] < band_1s["lower_band_1sigma"]


def test_lo_formula_value():
    """Sanity check the Lo (2002) formula at a known point.

    SR=0.4, N=720 → SE = sqrt((1 + 0.5·0.16) / 720) = sqrt(1.08/720) ≈ 0.0387
    """
    se = ot.sr_standard_error(0.4, 720)
    assert abs(se - 0.0387) < 0.001
