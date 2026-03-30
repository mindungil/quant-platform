from app.core.engine import build_summary


def test_build_summary_contains_services() -> None:
    result = build_summary()
    assert "portfolio" in result["services"]
