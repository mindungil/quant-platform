from app.db.repository import StrategyRepository
from app.models.strategy import StrategyCreate


def test_repository_can_promote_single_active_strategy() -> None:
    repo = StrategyRepository()
    created = repo.create(
        StrategyCreate(
            name="Second",
            asset_type="crypto",
            indicators=["rsi_14"],
            weights={"rsi": 1.0},
            thresholds={"entry": 0.6},
        )
    )

    repo.update_status(created.id, "ACTIVE")

    assert repo.get_active("crypto").id == created.id
