from app.core.crypto import decrypt, encrypt
from app.models.credential import CredentialCreate, CredentialResponse


class CredentialRepository:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str], tuple[str, str]] = {}

    def save(self, payload: CredentialCreate) -> CredentialResponse:
        self._items[(payload.user_id, payload.exchange)] = (
            encrypt(payload.api_key),
            encrypt(payload.api_secret),
        )
        return self.get(payload.user_id, payload.exchange)

    def get(self, user_id: str, exchange: str) -> CredentialResponse | None:
        value = self._items.get((user_id, exchange))
        if value is None:
            return None
        api_key, api_secret = value
        return CredentialResponse(
            user_id=user_id,
            exchange=exchange,
            api_key=decrypt(api_key),
            api_secret=decrypt(api_secret),
        )


credential_repository = CredentialRepository()
