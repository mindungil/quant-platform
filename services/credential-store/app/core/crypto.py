import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def _key_bytes() -> bytes:
    raw = os.getenv("CREDENTIAL_ENCRYPTION_KEY", "0123456789abcdef0123456789abcdef")
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
