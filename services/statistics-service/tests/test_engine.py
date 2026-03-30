from app.core.engine import compute_statistics
from app.models.statistics import StatisticsInput


def test_statistics_detects_negative_drift() -> None:
    result = compute_statistics(StatisticsInput(trade_pnls=[-1.0, 0.2], expected_return=0.0))
    assert result.drift_detected is True
