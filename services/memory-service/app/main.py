import os

from fastapi import FastAPI

from app.api.routes import router
from shared.health import check_sql, install_health_endpoints


app = FastAPI(title="memory-service", version="0.1.0")

_POSTGRES_URL = os.getenv(
    "POSTGRES_URL", "postgresql+psycopg://postgres:postgres@db:5432/platform"
)
install_health_endpoints(
    app,
    service="memory-service",
    readiness_checks={"postgres": lambda: check_sql("postgres", _POSTGRES_URL)},
)
app.include_router(router)
