from datetime import UTC, datetime

from app.models.external_data import ExternalContextSnapshot


def _scaled_value(asset: str, salt: str, minimum: float, maximum: float) -> float:
    raw = sum(ord(char) for char in f"{asset}:{salt}") % 100
    return round(minimum + ((maximum - minimum) * raw / 99), 4)


def build_external_context(asset: str) -> ExternalContextSnapshot:
    news_sentiment = _scaled_value(asset, "news", -1.0, 1.0)
    onchain_score = _scaled_value(asset, "onchain", -1.0, 1.0)
    macro_risk_score = _scaled_value(asset, "macro", -1.0, 1.0)
    fear_greed_index = int(_scaled_value(asset, "fear-greed", 5, 95))

    return ExternalContextSnapshot(
        asset=asset,
        timestamp=datetime.now(UTC),
        news_sentiment=news_sentiment,
        onchain_score=onchain_score,
        macro_risk_score=macro_risk_score,
        fear_greed_index=fear_greed_index,
        components={
            "news_sentiment": news_sentiment,
            "onchain_score": onchain_score,
            "macro_risk_score": macro_risk_score,
            "fear_greed_bias": round((fear_greed_index - 50) / 50, 4),
        },
        missing_fields=[],
    )
