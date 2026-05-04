from app.db.repository import _next_position_state, portfolio_repository
from app.models.portfolio import PositionUpdate


def test_portfolio_applies_fill() -> None:
    snapshot = portfolio_repository.apply(PositionUpdate(user_id="u1", asset="BTCUSDT", side="BUY", quantity=0.1))
    assert snapshot.positions["BTCUSDT"] == 0.1


def test_next_position_state_flips_and_resets_average_entry() -> None:
    qty, avg = _next_position_state(1.0, 100.0, "SELL", 2.0, 120.0)
    assert qty == -1.0
    assert avg == 120.0


def test_next_position_state_reduces_without_changing_average_entry() -> None:
    qty, avg = _next_position_state(2.0, 100.0, "SELL", 0.5, 110.0)
    assert qty == 1.5
    assert avg == 100.0
