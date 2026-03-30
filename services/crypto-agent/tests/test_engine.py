from datetime import UTC, datetime

from app.core import engine
from app.models.agent import MemoryRecord, MemorySearchResponse, SignalSnapshot, StrategySnapshot


class StubSignalClient:
    def get_latest_signal(self, asset: str) -> SignalSnapshot:
        return SignalSnapshot(
            asset=asset,
            signal_score=0.81,
            threshold=0.6,
            threshold_crossed=True,
            direction="BUY",
            components={"rsi": 0.8, "macd": 1.0},
            feature_timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        )


class StubStrategyClient:
    def get_active_strategy(self, asset_type: str) -> StrategySnapshot:
        return StrategySnapshot(
            id="strategy-1",
            name="Momentum",
            asset_type=asset_type,
            indicators=["rsi_14", "macd"],
            weights={"rsi": 0.5, "macd": 0.5},
            thresholds={"entry": 0.6},
            version="v1",
            status="ACTIVE",
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


def test_run_decision_loop_records_decision(monkeypatch) -> None:
    stub_memory = StubMemoryClient()
    monkeypatch.setattr(engine, "signal_client", StubSignalClient())
    monkeypatch.setattr(engine, "strategy_client", StubStrategyClient())
    monkeypatch.setattr(engine, "memory_client", stub_memory)

    decision = engine.run_decision_loop("BTCUSDT")

    assert decision.asset == "BTCUSDT"
    assert decision.action == "BUY"
    assert stub_memory.recorded
