from collections import defaultdict

from app.models.feature import CandlePayload, FeatureResponse


class CandleRepository:
    def __init__(self) -> None:
        self._items: dict[str, list[CandlePayload]] = defaultdict(list)

    def add(self, asset: str, candle: CandlePayload) -> None:
        self._items[asset].append(candle)
        self._items[asset] = sorted(self._items[asset], key=lambda item: item.timestamp)[-500:]

    def get(self, asset: str) -> list[CandlePayload]:
        return self._items[asset]


class FeatureRepository:
    def __init__(self) -> None:
        self._items: dict[str, list[FeatureResponse]] = defaultdict(list)

    def save(self, asset: str, feature: FeatureResponse) -> None:
        self._items[asset].append(feature)

    def get_latest(self, asset: str) -> FeatureResponse | None:
        history = self._items.get(asset, [])
        return history[-1] if history else None

    def get_history(self, asset: str) -> list[FeatureResponse]:
        return self._items.get(asset, [])


candle_repository = CandleRepository()
feature_repository = FeatureRepository()
