import json
from pathlib import Path

from app.api import routes
from app.core.evaluation_store import EvaluationStore
from app.models.auth import GatewayPrincipal


def _admin() -> GatewayPrincipal:
    return GatewayPrincipal(
        user_id="admin-1",
        email="admin@example.com",
        roles=["admin"],
        forwarded_headers={"X-User-ID": "admin-1"},
    )


def _seed_cycle(base_dir: Path) -> None:
    state_dir = base_dir.parent / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    cycle_dir = base_dir / "cycle_01"
    cycle_dir.mkdir(parents=True, exist_ok=True)
    latest = base_dir / "latest"
    if latest.exists():
        latest.unlink()
    latest.symlink_to(cycle_dir.name)
    (state_dir / "quant_cycle_status.json").write_text(json.dumps({
        "running": False,
        "active_cycle_id": "cycle_01",
        "phase": "completed",
    }))
    (cycle_dir / "status.json").write_text(json.dumps({
        "cycle_id": "cycle_01",
        "phase": "completed",
    }))
    (cycle_dir / "realtime_summary.json").write_text(json.dumps({
        "runtime_ops": {"service_uptime_pct": 99.5, "websocket_replay_success": True},
    }))
    (cycle_dir / "historical_summary.json").write_text(json.dumps({
        "historical_replay": {"sharpe": {"mean": 0.72}},
    }))
    (cycle_dir / "scorecard.json").write_text(json.dumps({
        "verdict": "hold",
        "blended_score": {"value": 68.3},
    }))
    (cycle_dir / "failure_report.json").write_text(json.dumps([
        {"category": "runtime", "severity": "medium", "symptom": "feed_stale", "impact": "signal freshness degraded"}
    ]))


def test_evaluation_store_builds_posture(tmp_path: Path) -> None:
    base_dir = tmp_path / "cycles"
    _seed_cycle(base_dir)
    store = EvaluationStore(base_dir)

    posture = store.build_posture()

    assert posture["completed_cycles"] == 1
    assert posture["latest_cycle_id"] == "cycle_01"
    assert posture["latest_verdict"] == "hold"
    assert posture["latest_blended_score"]["value"] == 68.3


def test_admin_evaluation_routes_return_latest_cycle(tmp_path: Path, monkeypatch) -> None:
    base_dir = tmp_path / "cycles"
    _seed_cycle(base_dir)
    monkeypatch.setattr(routes, "evaluation_store", EvaluationStore(base_dir))
    principal = _admin()

    posture = routes.admin_evaluation_posture(principal)
    realtime = routes.admin_evaluation_realtime_summary(principal)
    failures = routes.admin_evaluation_failures(principal)
    cycle = routes.admin_evaluation_cycle("cycle_01", principal)

    assert posture["latest_cycle_id"] == "cycle_01"
    assert realtime["runtime_ops"]["service_uptime_pct"] == 99.5
    assert failures["count"] == 1
    assert cycle["scorecard"]["verdict"] == "hold"
