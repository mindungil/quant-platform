from app.db.repository import CredentialRepository
from app.models.credential import CredentialCreate


def test_credentials_roundtrip() -> None:
    repo = CredentialRepository()
    repo.save(CredentialCreate(user_id="u1", exchange="binance", api_key="key", api_secret="secret"))
    loaded = repo.get("u1", "binance")
    assert loaded.api_key == "key"
    assert loaded.api_secret == "secret"
