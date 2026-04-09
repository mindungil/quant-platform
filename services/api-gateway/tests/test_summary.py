from app.core.summary import gateway_summary


def test_gateway_summary_lists_realtime_topics() -> None:
    result = gateway_summary()
    assert "order.filled.*" in result["realtime_topics"]


def test_gateway_summary_exposes_user_propagation_header() -> None:
    result = gateway_summary()
    assert result["user_propagation_header"] == "X-User-ID"


def test_gateway_summary_exposes_websocket_bridge() -> None:
    result = gateway_summary()
    assert result["websocket_bridge"] == "/ws?token=<jwt>"


def test_gateway_summary_lists_admin_routes() -> None:
    result = gateway_summary()
    assert "/admin/users" in result["authenticated_routes"]
