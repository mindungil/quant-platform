from app.core.summary import gateway_summary


def test_gateway_summary_lists_realtime_topics() -> None:
    result = gateway_summary()
    assert "order.filled.*" in result["realtime_topics"]
