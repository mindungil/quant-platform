from app.core.crypto import decrypt, encrypt
from app.models.credential import CredentialCreate, CredentialMaskedResponse, CredentialResponse


class CredentialRepository:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str], dict[str, str | bool | None]] = {}

    def _mask(self, value: str) -> str:
        if len(value) <= 4:
            return "*" * len(value)
        return f"{value[:2]}***{value[-2:]}"

    def save(self, payload: CredentialCreate) -> CredentialMaskedResponse:
        self._items[(payload.user_id, payload.exchange)] = {
            "api_key": encrypt(payload.api_key),
            "api_secret": encrypt(payload.api_secret),
            "label": payload.label,
            "sandbox": payload.sandbox,
        }
        return self.get_masked(payload.user_id, payload.exchange)

    def get(self, user_id: str, exchange: str) -> CredentialResponse | None:
        value = self._items.get((user_id, exchange))
        if value is None:
            return None
        return CredentialResponse(
            user_id=user_id,
            exchange=exchange,
            label=value["label"],
            sandbox=bool(value["sandbox"]),
            api_key=decrypt(str(value["api_key"])),
            api_secret=decrypt(str(value["api_secret"])),
        )

    def get_masked(self, user_id: str, exchange: str) -> CredentialMaskedResponse | None:
        credential = self.get(user_id, exchange)
        if credential is None:
            return None
        return CredentialMaskedResponse(
            user_id=credential.user_id,
            exchange=credential.exchange,
            label=credential.label,
            sandbox=credential.sandbox,
            api_key_masked=self._mask(credential.api_key),
            api_secret_masked=self._mask(credential.api_secret),
        )


credential_repository = CredentialRepository()
