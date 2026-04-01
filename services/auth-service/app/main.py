from fastapi import FastAPI

from app.api.routes import router
from app.db.repository import auth_repository

app = FastAPI(title="auth-service", version="0.1.0")
app.include_router(router)


@app.on_event("startup")
def bootstrap_defaults() -> None:
    auth_repository.bootstrap_admin()
