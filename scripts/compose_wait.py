from __future__ import annotations

from common import load_env, service_url, wait_for_http


def main() -> None:
    load_env()
    services = {
        "auth-service": service_url("HOST_AUTH_SERVICE_BASE_URL", "http://localhost:8019"),
        "market-data": service_url("HOST_MARKET_DATA_BASE_URL", "http://localhost:8001"),
        "feature-store": service_url("HOST_FEATURE_STORE_BASE_URL", "http://localhost:8002"),
        "signal-service": service_url("HOST_SIGNAL_SERVICE_BASE_URL", "http://localhost:8003"),
        "crypto-agent": service_url("HOST_CRYPTO_AGENT_BASE_URL", "http://localhost:8006"),
        "order-service": service_url("HOST_ORDER_SERVICE_BASE_URL", "http://localhost:8011"),
        "portfolio-service": service_url("HOST_PORTFOLIO_SERVICE_BASE_URL", "http://localhost:8012"),
        "statistics-service": service_url("HOST_STATISTICS_SERVICE_BASE_URL", "http://localhost:8013"),
        "api-gateway": service_url("HOST_API_GATEWAY_BASE_URL", "http://localhost:8017"),
        "frontend": service_url("HOST_FRONTEND_BASE_URL", "http://localhost:8018"),
    }
    for name, base in services.items():
        target = base if name == "frontend" else f"{base}/health"
        wait_for_http(target)
        print(f"ready: {name}")


if __name__ == "__main__":
    main()
