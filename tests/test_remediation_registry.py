from scripts.research.remediation_registry import select_remediations


def test_select_remediations_picks_runtime_and_activity_refresh() -> None:
    actions = select_remediations(
        cycle_id="cycle_01",
        scorecard={"blended_score": {"value": 20.0}},
        realtime_summary={
            "runtime_ops": {"service_uptime_pct": 50.0, "websocket_replay_success": False},
            "signal_alpha": {"actionable_signal_rate": 0.0, "signal_staleness_seconds": 9999.0, "degraded_mode_rate": 1.0},
        },
        historical_summary={"historical_replay": {"sharpe": {"mean": -0.2}}},
        execution_quality={"aggregate": {"fill_rate": 0.0, "reject_rate": 0.4}, "total_orders": 0},
        verification={"all_passed": False},
        failures=[
            {"symptom": "health_snapshot_failed"},
            {"symptom": "no_realtime_samples"},
        ],
        max_actions=3,
    )

    ids = [item["action_id"] for item in actions]
    assert "runtime_recover" in ids
    assert "activity_refresh" in ids
    assert len(actions) <= 3


def test_select_remediations_defaults_to_shadow_maintenance() -> None:
    actions = select_remediations(
        cycle_id="cycle_02",
        scorecard={"blended_score": {"value": 88.0}},
        realtime_summary={
            "runtime_ops": {"service_uptime_pct": 99.9, "websocket_replay_success": True},
            "signal_alpha": {"actionable_signal_rate": 0.3, "signal_staleness_seconds": 60.0, "degraded_mode_rate": 0.0},
        },
        historical_summary={"historical_replay": {"sharpe": {"mean": 1.1}}},
        execution_quality={"aggregate": {"fill_rate": 0.9, "reject_rate": 0.02}, "total_orders": 12},
        verification={"all_passed": True},
        failures=[],
        max_actions=3,
    )

    assert actions[0]["action_id"] == "shadow_posture_keepalive"
