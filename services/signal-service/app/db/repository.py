from collections import defaultdict

from app.models.signal import SignalEvaluationResponse


class SignalRepository:
    def __init__(self) -> None:
        self._items: dict[str, list[SignalEvaluationResponse]] = defaultdict(list)

    def save(self, asset: str, evaluation: SignalEvaluationResponse) -> None:
        self._items[asset].append(evaluation)

    def get_latest(self, asset: str) -> SignalEvaluationResponse | None:
        history = self._items.get(asset, [])
        return history[-1] if history else None

    def list_latest(self) -> list[SignalEvaluationResponse]:
        return [history[-1] for history in self._items.values() if history]


signal_repository = SignalRepository()
