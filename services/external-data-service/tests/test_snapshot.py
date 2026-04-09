from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.core import snapshot
from app.core.snapshot import (
    _cache,
    _fetch_fear_greed,
    _fetch_news_sentiment,
    _fetch_onchain_score,
    build_external_context,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    _cache.clear()
    yield
    _cache.clear()


def _mock_response(json_data, status_code=200):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.raise_for_status.return_value = None
    return resp


class TestFetchFearGreed:
    @patch("app.core.snapshot.httpx.get")
    def test_returns_value_and_normalized(self, mock_get):
        mock_get.return_value = _mock_response({
            "data": [{"value": "11", "value_classification": "Extreme Fear"}],
        })
        value, norm = _fetch_fear_greed()
        assert value == 11
        assert norm == -0.8

    @patch("app.core.snapshot.httpx.get")
    def test_cache_hit(self, mock_get):
        mock_get.return_value = _mock_response({
            "data": [{"value": "50", "value_classification": "Neutral"}],
        })
        _fetch_fear_greed()
        _fetch_fear_greed()
        assert mock_get.call_count == 1

    @patch("app.core.snapshot.httpx.get", side_effect=httpx.ConnectError("timeout"))
    def test_fallback_on_error(self, mock_get):
        value, norm = _fetch_fear_greed()
        assert value == 50
        assert norm == 0.0


class TestFetchNewsSentiment:
    @patch("app.core.snapshot.httpx.get")
    def test_positive_sentiment(self, mock_get):
        mock_get.return_value = _mock_response({
            "results": [
                {"votes": {"positive": 8, "negative": 2}},
                {"votes": {"positive": 5, "negative": 1}},
            ],
        })
        sent = _fetch_news_sentiment("BTCUSDT")
        # (8+5 - 2-1) / (8+5+2+1) = 10/16 = 0.625
        assert 0.6 < sent < 0.7

    @patch("app.core.snapshot.httpx.get")
    def test_empty_results(self, mock_get):
        mock_get.return_value = _mock_response({"results": []})
        assert _fetch_news_sentiment("ETHUSDT") == 0.0

    @patch("app.core.snapshot.httpx.get", side_effect=Exception("fail"))
    def test_fallback_on_error(self, mock_get):
        assert _fetch_news_sentiment("BTCUSDT") == 0.0

    @patch("app.core.snapshot.httpx.get")
    def test_cache_hit(self, mock_get):
        mock_get.return_value = _mock_response({
            "results": [{"votes": {"positive": 1, "negative": 0}}],
        })
        _fetch_news_sentiment("BTCUSDT")
        _fetch_news_sentiment("BTCUSDT")
        assert mock_get.call_count == 1


class TestFetchOnchainScore:
    @patch("app.core.snapshot.httpx.get")
    def test_btc_normal_tx_count(self, mock_get):
        mock_get.return_value = _mock_response({"n_tx": 400_000})
        score = _fetch_onchain_score("BTCUSDT")
        # min(400000/400000, 1.0) * 2 - 1 = 1.0
        assert score == pytest.approx(1.0)

    @patch("app.core.snapshot.httpx.get")
    def test_btc_low_tx_count(self, mock_get):
        mock_get.return_value = _mock_response({"n_tx": 200_000})
        score = _fetch_onchain_score("BTCUSDT")
        # min(200000/400000, 1.0) * 2 - 1 = 0.0
        assert score == pytest.approx(0.0)

    def test_non_btc_returns_zero(self):
        assert _fetch_onchain_score("ETHUSDT") == 0.0

    @patch("app.core.snapshot.httpx.get", side_effect=Exception("fail"))
    def test_fallback_on_error(self, mock_get):
        assert _fetch_onchain_score("BTCUSDT") == 0.0


class TestBuildExternalContext:
    @patch("app.core.snapshot._fetch_onchain_score", return_value=0.2)
    @patch("app.core.snapshot._fetch_news_sentiment", return_value=0.5)
    @patch("app.core.snapshot._fetch_fear_greed", return_value=(11, -0.8))
    def test_full_snapshot(self, mock_fg, mock_news, mock_onchain):
        snap = build_external_context("BTCUSDT")
        assert snap.asset == "BTCUSDT"
        assert snap.fear_greed_index == 11
        assert snap.news_sentiment == 0.5
        assert snap.onchain_score == 0.2
        assert snap.macro_risk_score == 0.8  # -(-0.8)
        assert snap.missing_fields == []
        assert "news_sentiment" in snap.components
