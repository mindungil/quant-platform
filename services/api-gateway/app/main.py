from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.routes import router
from app.api.admin_dlq import router as dlq_router
from app.core.config import settings
from shared.health import check_redis, check_tcp
from shared.observability import install_http_observability, startup_dependency_guard

app = FastAPI(title="api-gateway", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://quent.kro.kr",
        "http://localhost:8018",
        "http://localhost:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

install_http_observability(app, "api-gateway")
app.include_router(router)
app.include_router(dlq_router)


@app.on_event("startup")
def startup_checks() -> None:
    startup_dependency_guard(
        service_name="api-gateway",
        check_fns={
            "redis": lambda: check_redis("redis", settings.redis_url),
            "auth-service": lambda: check_tcp("auth-service", settings.auth_service_base_url, default_port=8000),
        },
    )
