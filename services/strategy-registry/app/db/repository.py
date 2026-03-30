from app.models.strategy import Strategy, StrategyCreate


class StrategyRepository:
    def __init__(self) -> None:
        self._items: dict[str, Strategy] = {}
        self._seed_default()

    def _seed_default(self) -> None:
        strategy = Strategy(
            user_id="bootstrap",
            name="Crypto Momentum Bootstrap",
            asset_type="crypto",
            indicators=["rsi_14", "macd", "sma_20", "vwap"],
            weights={"rsi": 0.25, "macd": 0.25, "sma_20": 0.25, "vwap": 0.25},
            thresholds={"entry": 0.6, "exit": -0.6},
            version="v1",
            status="ACTIVE",
            backtest_results={"source": "bootstrap_seed"},
        )
        self._items[strategy.id] = strategy

    def create(self, payload: StrategyCreate) -> Strategy:
        strategy = Strategy(**payload.model_dump())
        self._items[strategy.id] = strategy
        return strategy

    def get(self, strategy_id: str) -> Strategy | None:
        return self._items.get(strategy_id)

    def get_active(self, asset_type: str) -> Strategy | None:
        for strategy in self._items.values():
            if strategy.user_id == "bootstrap" and strategy.asset_type == asset_type and strategy.status == "ACTIVE":
                return strategy
        return None

    def get_active_for_user(self, asset_type: str, user_id: str) -> Strategy | None:
        for strategy in self._items.values():
            if strategy.asset_type == asset_type and strategy.status == "ACTIVE" and strategy.user_id == user_id:
                return strategy
        return self.get_active(asset_type)

    def update_status(self, strategy_id: str, status: str) -> Strategy | None:
        strategy = self._items.get(strategy_id)
        if strategy is None:
            return None
        if status == "ACTIVE":
            for item in self._items.values():
                if item.asset_type == strategy.asset_type and item.id != strategy.id and item.status == "ACTIVE":
                    item.status = "DEPRECATED"
        strategy.status = status
        return strategy


strategy_repository = StrategyRepository()
