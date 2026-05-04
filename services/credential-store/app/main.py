from fastapi import FastAPI

from app.api.routes import router
from app.core.crypto import assert_secure_key

# Fail fast if encryption key is missing / using the insecure default.
# Prevents silent plaintext-equivalent secret storage in production.
# Dev/CI can opt out with ALLOW_INSECURE_CREDENTIAL_KEY=true.
assert_secure_key()

app = FastAPI(title="credential-store", version="0.1.0")
app.include_router(router)
