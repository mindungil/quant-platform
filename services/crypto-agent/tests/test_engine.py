from datetime import UTC, datetime

from app.core import engine
from app.models.agent import MemoryRecord, MemorySearchResponse, SignalSnapshot, StrategySnapshot


class StubSignalClient:
    def get_latest_signal(self, asset: str, *, user_id: str | None = None) -> SignalSnapshot:
        return SignalSnapshot(
            asset=asset,
            signal_score=0.81,
            threshold=0.6,
            threshold_crossed=True,
            direction="BUY",
            components={"rsi": 0.8, "macd": 1.0},
            feature_timestamp=datetime.now(UTC),
        )


class StubSignalClientStale:
    """Returns a signal with a stale feature_timestamp (10 minutes ago)."""

    def get_latest_signal(self, asset: str, *, user_id: str | None = None) -> SignalSnapshot:
        from datetime import timedelta

        return SignalSnapshot(
            asset=asset,
            signal_score=0.81,
            threshold=0.6,
            threshold_crossed=True,
            direction="BUY",
            components={"rsi": 0.8, "macd": 1.0},
            feature_timestamp=datetime.now(UTC) - timedelta(minutes=10),
        )


class StubStrategyClient:
    def get_active_strategy(self, asset_type: str, *, user_id: str | None = None) -> StrategySnapshot:
        return StrategySnapshot(
            id="strategy-1",
            user_id=user_id or "bootstrap",
            name="Momentum",
            asset_type=asset_type,
            indicators=["rsi_14", "macd"],
            weights={"rsi": 0.5, "macd": 0.5},
            thresholds={"entry": 0.6},
            version="v1",
            status="ACTIVE",
        )


class StubStrategyClientInactive:
    def get_active_strategy(self, asset_type: str, *, user_id: str | None = None) -> StrategySnapshot:
        return StrategySnapshot(
            id="strategy-1",
            user_id=user_id or "bootstrap",
            name="Momentum",
            asset_type=asset_type,
            indicators=["rsi_14", "macd"],
            weights={"rsi": 0.5, "macd": 0.5},
            thresholds={"entry": 0.6},
            version="v1",
            status="PAUSED",
        )


class StubMemoryClient:
    def __init__(self) -> None:
        self.recorded: list[MemoryRecord] = []

    def search(self, request):
        return MemorySearchResponse(
            query=request,
            items=[
                {
                    "score": 0.9,
                    "record": MemoryRecord(
                        id="memory-1",
                        asset="BTCUSDT",
                        asset_type="crypto",
                        signal_score=0.75,
                        action="BUY",
                        strategy_id="strategy-1",
                        reasoning="past success",
                    ),
                }
            ],
        )

    def record(self, record: MemoryRecord) -> MemoryRecord:
        self.recorded.append(record)
        return record


class StubLlmGatewayClient:
    def generate_reasoning(self, **kwargs) -> str:
        return "LLM reasoning"


class StubPublisher:
    def publish_agent_action(self, decision, order_request) -> None:
        return None


def _patch_engine(monkeypatch, *, signal_client=None, strategy_client=None):
    """Apply standard stubs to the engine module."""
    stub_memory = StubMemoryClient()
    monkeypatch.setattr(engine, "signal_client", signal_client or StubSignalClient())
    monkeypatch.setattr(engine, "strategy_client", strategy_client or StubStrategyClient())
    monkeypatch.setattr(engine, "memory_client", stub_memory)
    monkeypatch.setattr(engine, "llm_gateway_client", StubLlmGatewayClient())
    monkeypatch.setattr(engine, "publisher", StubPublisher())
    return stub_memory


def test_run_decision_loop_records_decision(monkeypatch) -> None:
    stub_memory = _patch_engine(monkeypatch)

    decision = engine.run_decision_loop("BTCUSDT")

    assert decision.asset == "BTCUSDT"
    assert decision.action == "BUY"
    assert decision.reasoning == "LLM reasoning"
    assert decision.user_id
    assert stub_memory.recorded


def test_decision_phases_are_tracked(monkeypatch) -> None:
    """All 6 phases should be recorded on the decision."""
    _patch_engine(monkeypatch)

    decision = engine.run_decision_loop("BTCUSDT")

    phase_names = [p.name for p in decision.decision_phases]
    assert "gather" in phase_names
    assert "retrieve" in phase_names
    assert "select" in phase_names
    assert "check" in phase_names
    assert "execute" in phase_names
    assert "record" in phase_names

    for phase in decision.decision_phases:
        assert phase.status == "completed", f"phase {phase.name} not completed: {phase.status}"
        assert phase.duration_ms is not None
        assert phase.duration_ms >= 0


def test_check_phase_warns_on_stale_signal(monkeypatch) -> None:
    """Check phase should include a staleness warning when signal is old."""
    _patch_engine(monkeypatch, signal_client=StubSignalClientStale())

    decision = engine.run_decision_loop("BTCUSDT")

    check_phase = next(p for p in decision.decision_phases if p.name == "check")
    assert check_phase.status == "completed"
    assert check_phase.detail is not None
    assert "stale" in check_phase.detail


def test_check_phase_warns_on_inactive_strategy(monkeypatch) -> None:
    """Check phase should warn when strategy is not ACTIVE."""
    _patch_engine(monkeypatch, strategy_client=StubStrategyClientInactive())

    decision = engine.run_decision_loop("BTCUSDT")

    check_phase = next(p for p in decision.decision_phases if p.name == "check")
    assert check_phase.detail is not None
    assert "ACTIVE" in check_phase.detail


def test_check_phase_passes_cleanly(monkeypatch) -> None:
    """Check phase should pass with no warnings for a fresh signal and active strategy."""
    _patch_engine(monkeypatch)

    decision = engine.run_decision_loop("ETHUSDT")

    check_phase = next(p for p in decision.decision_phases if p.name == "check")
    assert check_phase.detail == "all checks passed"
