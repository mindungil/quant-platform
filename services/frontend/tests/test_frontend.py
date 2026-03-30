from app.main import index


def test_frontend_contains_title() -> None:
    assert "Quant Command Deck" in index()


def test_frontend_contains_gateway_stream_controls() -> None:
    html = index()
    assert "Connect Stream" in html
    assert ":8017/ws?token=" in html
