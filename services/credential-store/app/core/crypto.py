import base64
import logging
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

logger = logging.getLogger("credential-store.crypto")

_INSECURE_DEFAULT = "0123456789abcdef0123456789abcdef"
_INSECURE_ESCAPE_HATCH = "ALLOW_INSECURE_CREDENTIAL_KEY"


class InsecureCredentialKeyError(RuntimeError):
    """Raised when CREDENTIAL_ENCRYPTION_KEY is missing/insecure and the
    explicit ALLOW_INSECURE_CREDENTIAL_KEY=true escape hatch isn't set.

    Behavior:
      - production:    NEVER set ALLOW_INSECURE_CREDENTIAL_KEY → raises
      - test/CI:       set ALLOW_INSECURE_CREDENTIAL_KEY=true → uses default
      - dev local:     set CREDENTIAL_ENCRYPTION_KEY=$(openssl rand -hex 32)
    """


def assert_secure_key() -> None:
    """Startup-time guard. Call this from main.py BEFORE the app starts
    accepting traffic. Fails fast so an operator notices at deploy time
    rather than discovering plaintext-equivalent secrets in the DB later.
    """
    raw = os.getenv("CREDENTIAL_ENCRYPTION_KEY", "")
    insecure = (not raw) or (raw == _INSECURE_DEFAULT)
    if not insecure:
        return
    escape = os.getenv(_INSECURE_ESCAPE_HATCH, "").lower() == "true"
    if escape:
        logger.warning(
            "CREDENTIAL_ENCRYPTION_KEY missing/default but %s=true — running with "
            "INSECURE encryption (dev/CI only). Production deploys MUST unset this.",
            _INSECURE_ESCAPE_HATCH,
        )
        return
    raise InsecureCredentialKeyError(
        "CREDENTIAL_ENCRYPTION_KEY is missing or using the insecure default. "
        "Refusing to start. Either:\n"
        "  - production: export CREDENTIAL_ENCRYPTION_KEY=$(openssl rand -hex 32)\n"
        f"  - dev/CI:     export {_INSECURE_ESCAPE_HATCH}=true (acknowledges the risk)"
    )


def _key_bytes() -> bytes:
    raw = os.getenv("CREDENTIAL_ENCRYPTION_KEY", "")
    if not raw or raw == _INSECURE_DEFAULT:
        # In normal flow assert_secure_key() at startup blocks this path.
        # Reaching here means the escape hatch is set OR an import happened
        # before the startup hook ran. Use default but never silently pretend
        # it's secure.
        if os.getenv(_INSECURE_ESCAPE_HATCH, "").lower() != "true":
            raise InsecureCredentialKeyError(
                "encrypt/decrypt called with insecure default key and no "
                f"{_INSECURE_ESCAPE_HATCH}=true escape hatch — refusing."
            )
        raw = _INSECURE_DEFAULT
    return raw.encode("utf-8")[:32].ljust(32, b"0")


def encrypt(value: str) -> str:
    aes = AESGCM(_key_bytes())
    nonce = os.urandom(12)
    encrypted = aes.encrypt(nonce, value.encode("utf-8"), None)
    return base64.b64encode(nonce + encrypted).decode("utf-8")


def decrypt(value: str) -> str:
    aes = AESGCM(_key_bytes())
    decoded = base64.b64decode(value.encode("utf-8"))
    nonce, ciphertext = decoded[:12], decoded[12:]
    return aes.decrypt(nonce, ciphertext, None).decode("utf-8")
