from fastapi import FastAPI

from app.api.routes import router
from app.core.config import settings
from app.db.repository import auth_repository
from shared.health import check_sql
from shared.observability import install_http_observability, startup_dependency_guard

app = FastAPI(title="auth-service", version="0.1.0")
install_http_observability(app, "auth-service")
app.include_router(router)


@app.on_event("startup")
def bootstrap_defaults() -> None:
    if not settings.jwt_secret:
        raise RuntimeError("JWT_SECRET environment variable must be set to a strong random value")
    startup_dependency_guard(
        service_name="auth-service",
        check_fns={
            "postgres": lambda: check_sql("postgres", settings.postgres_url),
        },
    )
    auth_repository.bootstrap_admin()
