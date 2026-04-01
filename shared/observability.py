from __future__ import annotations

from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, Request
from prometheus_client import Counter, Gauge, Histogram

from shared.health import require_health
from shared.logging import get_logger
from shared.request_context import reset_request_context, set_request_context
from shared.runtime import validate_required_env

REQUEST_COUNTER = Counter(
    "quant_http_requests_total",
    "HTTP requests processed by service, method, path and status.",
    ["service", "method", "path", "status_code"],
)
REQUEST_LATENCY = Histogram(
    "quant_http_request_duration_seconds",
    "HTTP request duration in seconds by service, method and path.",
    ["service", "method", "path"],
)
INFLIGHT_REQUESTS = Gauge(
    "quant_http_inflight_requests",
    "Current inflight HTTP requests by service.",
    ["service"],
)


def install_http_observability(app: FastAPI, service_name: str) -> None:
    logger = get_logger(service_name)

    @app.middleware("http")
    async def attach_request_context(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or str(uuid4())
        correlation_id = request.headers.get("X-Correlation-ID") or request_id
        user_id = request.headers.get("X-User-ID")
        tokens = set_request_context(
            request_id=request_id,
            correlation_id=correlation_id,
            user_id=user_id,
        )
        path = request.url.path
        method = request.method.upper()
        started_at = perf_counter()
        INFLIGHT_REQUESTS.labels(service=service_name).inc()
        try:
            response = await call_next(request)
            status_code = response.status_code
        except Exception:
            duration_ms = round((perf_counter() - started_at) * 1000, 3)
            REQUEST_COUNTER.labels(
                service=service_name,
                method=method,
                path=path,
                status_code="500",
            ).inc()
            REQUEST_LATENCY.labels(service=service_name, method=method, path=path).observe(duration_ms / 1000)
            logger.exception(
                "request_failed",
                extra={
                    "service": service_name,
                    "request_id": request_id,
                    "correlation_id": correlation_id,
                    "user_id": user_id,
                    "path": path,
                    "status_code": 500,
                    "duration_ms": duration_ms,
                },
            )
            raise
        finally:
            INFLIGHT_REQUESTS.labels(service=service_name).dec()
            reset_request_context(tokens)

        duration_ms = round((perf_counter() - started_at) * 1000, 3)
        REQUEST_COUNTER.labels(
            service=service_name,
            method=method,
            path=path,
            status_code=str(status_code),
        ).inc()
        REQUEST_LATENCY.labels(service=service_name, method=method, path=path).observe(duration_ms / 1000)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Correlation-ID"] = correlation_id
        logger.info(
            "request_complete",
            extra={
                "service": service_name,
                "request_id": request_id,
                "correlation_id": correlation_id,
                "user_id": user_id,
                "path": path,
                "status_code": status_code,
                "duration_ms": duration_ms,
            },
        )
        return response


def startup_dependency_guard(
    *,
    service_name: str,
    required_env: list[str] | None = None,
    checks: dict[str, dict] | None = None,
    check_fns: dict[str, callable] | None = None,
) -> None:
    import time
    import logging

    if required_env:
        validate_required_env(required_env)

    # If check_fns provided (lazy callables), use retry logic
    if check_fns:
        logger = logging.getLogger(service_name)
        retries = 10
        delay = 3.0
        for attempt in range(retries):
            results = {name: fn() for name, fn in check_fns.items()}
            all_ok = all(v.get("status") == "ok" for v in results.values())
            if all_ok:
                return
            failed = [k for k, v in results.items() if v.get("status") != "ok"]
            if attempt < retries - 1:
                logger.warning(
                    "startup check failed (attempt %d/%d), retrying in %.0fs: %s",
                    attempt + 1, retries, delay, failed,
                )
                time.sleep(delay)
        logger.warning("startup checks exhausted retries, starting in degraded mode")
        return

    # Legacy path: already-evaluated checks dict
    if checks:
        require_health(service_name, checks)
