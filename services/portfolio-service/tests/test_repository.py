from app.db.repository import portfolio_repository
from app.models.portfolio import PositionUpdate


def test_portfolio_applies_fill() -> None:
    snapshot = portfolio_repository.apply(PositionUpdate(user_id="u1", asset="BTCUSDT", side="BUY", quantity=0.1))
    assert snapshot.positions["BTCUSDT"] == 0.1
