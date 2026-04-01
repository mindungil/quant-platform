from __future__ import annotations

from common import load_env, request_json, service_url, wait_for_http


def main() -> None:
    load_env()
    auth_base = service_url("HOST_AUTH_SERVICE_BASE_URL", "http://localhost:8019")
    gateway_base = service_url("HOST_API_GATEWAY_BASE_URL", "http://localhost:8017")
    bootstrap_token = service_url("BOOTSTRAP_ADMIN_TOKEN", "dev-bootstrap-token")
    admin_email = service_url("BOOTSTRAP_ADMIN_EMAIL", "admin@quant.local")

    wait_for_http(f"{auth_base}/health")
    wait_for_http(f"{gateway_base}/health")

    request_json(
        "POST",
        f"{auth_base}/admin/bootstrap",
        headers={"X-Bootstrap-Token": bootstrap_token},
        expected_status=(200, 201),
    )
    print(f"Bootstrap admin ready: {admin_email}")


if __name__ == "__main__":
    main()
