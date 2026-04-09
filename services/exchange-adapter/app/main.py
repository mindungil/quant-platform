from fastapi import FastAPI
from app.api.routes import router
from app.core.config import settings
from shared.health import check_sql
from shared.observability import install_http_observability, startup_dependency_guard

app = FastAPI(title="exchange-adapter", version="0.1.0")
install_http_observability(app, "exchange-adapter")
app.include_router(router)


@app.on_event("startup")
def startup_checks() -> None:
    startup_dependency_guard(
        service_name="exchange-adapter",
        check_fns={
            "postgres": lambda: check_sql("postgres", settings.postgres_url),
        },
    )
