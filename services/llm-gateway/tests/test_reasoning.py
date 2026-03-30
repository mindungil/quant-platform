from app.core.reasoning import build_reasoning_text
from app.models.reasoning import ReasoningRequest


def test_reasoning_mentions_strategy_and_context() -> None:
    response = build_reasoning_text(
        ReasoningRequest(
            asset="BTCUSDT",
            signal_score=0.72,
            strategy_name="Momentum",
            memory_count=3,
            components={"rsi": 0.8, "macd": 1.0},
            external_context={"news_sentiment": 0.4},
        )
    )

    assert "Momentum" in response.reasoning
    assert "news_sentiment" in response.reasoning
