from datetime import datetime, timezone

UTC = timezone.utc

from app.core.scoring import build_signal_response
from app.db.repository import SignalRepository
from app.models.signal import ExternalContextSnapshot, FeatureSnapshot


def test_build_signal_response_crosses_buy_threshold() -> None:
    features = FeatureSnapshot(
        asset="BTCUSDT",
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        close=120,
        rsi_14=80,
        macd=2,
        macd_signal=1,
        sma_20=110,
        vwap=115,
    )

    response = build_signal_response(
        asset="BTCUSDT",
        features=features,
        threshold=0.6,
        external_context=ExternalContextSnapshot(
            asset="BTCUSDT",
            timestamp=datetime(2026, 1, 1, tzinfo=UTC),
            news_sentiment=0.6,
            onchain_score=0.4,
            macro_risk_score=0.2,
            fear_greed_index=70,
        ),
        external_signal_weight=0.35,
    )

    assert response.direction == "BUY"
    assert response.threshold_crossed is True
    assert "news_sentiment" in response.components


def test_signal_repository_returns_latest() -> None:
    repo = SignalRepository()
    first = build_signal_response(
        asset="BTCUSDT",
        features=FeatureSnapshot(
            asset="BTCUSDT",
            timestamp=datetime(2026, 1, 1, tzinfo=UTC),
            close=100,
            rsi_14=45,
            macd=1,
            macd_signal=2,
            sma_20=101,
            vwap=101,
        ),
        threshold=0.6,
    )
    second = build_signal_response(
        asset="BTCUSDT",
        features=FeatureSnapshot(
            asset="BTCUSDT",
            timestamp=datetime(2026, 1, 2, tzinfo=UTC),
            close=120,
            rsi_14=80,
            macd=2,
            macd_signal=1,
            sma_20=110,
            vwap=115,
        ),
        threshold=0.6,
    )

    repo.save("BTCUSDT", first)
    repo.save("BTCUSDT", second)

    assert repo.get_latest("BTCUSDT") == second
