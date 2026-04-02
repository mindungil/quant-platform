import httpx

from app.models.agent import SignalSnapshot
from shared.request_context import current_request_headers


class SignalClient:
    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")

    def get_latest_signal(self, asset: str, *, user_id: str | None = None) -> SignalSnapshot:
        headers = {**current_request_headers(), **({"X-User-ID": user_id} if user_id else {})}
        response = httpx.get(f"{self._base_url}/signals/{asset}/latest", headers=headers, timeout=5.0)
        if response.status_code == 404:
            # No cached signal — trigger evaluation first
            try:
                eval_resp = httpx.post(
                    f"{self._base_url}/signals/{asset}/evaluate",
                    headers=headers,
                    timeout=10.0,
                )
                if eval_resp.status_code == 200:
                    return SignalSnapshot.model_validate(eval_resp.json())
            except Exception:
                pass
            # Fallback: return a neutral signal
            from datetime import UTC, datetime
            return SignalSnapshot(
                asset=asset,
                timestamp=datetime.now(UTC),
                signal_score=0.0,
                threshold=0.6,
                threshold_crossed=False,
                direction="HOLD",
                components={},
                feature_timestamp=datetime.now(UTC),
            )
        response.raise_for_status()
        return SignalSnapshot.model_validate(response.json())
