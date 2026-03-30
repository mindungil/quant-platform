from app.core.snapshot import build_external_context


def test_external_context_is_stable_shape() -> None:
    snapshot = build_external_context("BTCUSDT")

    assert snapshot.asset == "BTCUSDT"
    assert "news_sentiment" in snapshot.components
    assert snapshot.missing_fields == []
