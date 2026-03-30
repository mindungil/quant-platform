from app.core.scoring import search_memories
from app.models.memory import MemoryRecord, MemorySearchRequest


def test_search_prioritizes_same_asset_and_action() -> None:
    records = [
        MemoryRecord(
            asset="BTCUSDT",
            asset_type="crypto",
            signal_score=0.82,
            action="BUY",
            strategy_id="momentum_v1",
            reasoning="prior btc breakout",
        ),
        MemoryRecord(
            asset="ETHUSDT",
            asset_type="crypto",
            signal_score=0.81,
            action="BUY",
            strategy_id="momentum_v1",
            reasoning="eth breakout",
        ),
    ]

    response = search_memories(
        records,
        MemorySearchRequest(asset="BTCUSDT", signal_score=0.8, action="BUY", strategy_id="momentum_v1"),
    )

    assert response[0].record.asset == "BTCUSDT"
