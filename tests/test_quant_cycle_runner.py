import importlib.util
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "scripts" / "research" / "quant_cycle_runner.py"


spec = importlib.util.spec_from_file_location("quant_cycle_runner", MODULE_PATH)
assert spec and spec.loader
quant_cycle_runner = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = quant_cycle_runner
spec.loader.exec_module(quant_cycle_runner)


def test_compute_blended_score_prefers_green_runtime() -> None:
    realtime = {
        "profitability": {"fee_adjusted_net_pnl": 12.0},
        "risk": {"max_drawdown": 0.08},
        "execution": {"reconcile_lag_seconds": 6.0},
        "signal_alpha": {"actionable_signal_rate": 0.42, "signal_staleness_seconds": 120.0, "degraded_mode_rate": 0.05},
        "runtime_ops": {"service_uptime_pct": 99.0, "websocket_replay_success": True},
    }
    historical = {
        "historical_replay": {
            "sharpe": {"mean": 0.85, "pct_positive": 0.8},
            "blowup_rate_pct": 1.0,
            "max_dd": {"mean": 0.14},
        }
    }
    execution_quality = {"aggregate": {"fill_rate": 0.91, "reject_rate": 0.02}}
    verification = {"all_passed": True}

    scorecard = quant_cycle_runner._compute_blended_score(realtime, historical, execution_quality, verification)

    assert scorecard["verdict"] in {"go", "hold"}
    assert scorecard["blended_score"]["value"] > 60
    assert scorecard["runtime_ops"] > 60


def test_summarize_execution_quality_handles_empty_report() -> None:
    summary = quant_cycle_runner._summarize_execution_quality({"total_orders": 0, "per_symbol": {}})

    assert summary["aggregate"]["fill_rate"] == 0.0
    assert summary["aggregate"]["symbols"] == 0
