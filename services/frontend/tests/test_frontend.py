from app.main import index


def test_frontend_contains_title() -> None:
    assert "Quant Command Deck" in index()
