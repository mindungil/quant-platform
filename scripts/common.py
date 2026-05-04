from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import requests


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_env() -> None:
    for candidate in (REPO_ROOT / ".env", REPO_ROOT / ".env.example"):
        if not candidate.exists():
            continue
        for raw_line in candidate.read_text().splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())
        return


def env(name: str, default: str) -> str:
    return os.getenv(name, default)


def service_url(name: str, default: str) -> str:
    return env(name, default).rstrip("/")


def wait_for_http(url: str, *, timeout_seconds: int = 120, interval_seconds: float = 2.0) -> None:
    deadline = time.time() + timeout_seconds
    last_error = "timeout"
    while time.time() < deadline:
        try:
            response = requests.get(url, timeout=3.0)
            if response.ok:
                return
            last_error = f"{response.status_code}: {response.text}"
        except requests.RequestException as exc:
            last_error = str(exc)
        time.sleep(interval_seconds)
    raise RuntimeError(f"service did not become ready: {url} ({last_error})")


def request_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
    expected_status: int | tuple[int, ...] = 200,
) -> Any:
    expected = (expected_status,) if isinstance(expected_status, int) else expected_status
    response = requests.request(method, url, headers=headers, json=payload, timeout=10.0)
    if response.status_code not in expected:
        raise RuntimeError(f"{method} {url} failed: {response.status_code} {response.text}")
    if not response.text:
        return {}
    return response.json()


def ensure_registered(gateway_base: str, *, email: str, password: str, display_name: str, plan: str = "premium") -> None:
    response = requests.post(
        f"{gateway_base}/auth/register",
        json={"email": email, "password": password, "display_name": display_name, "plan": plan},
        timeout=10.0,
    )
    if response.status_code in (200, 201, 409, 429):
        return
    raise RuntimeError(f"register failed: {response.status_code} {response.text}")


def login(gateway_base: str, *, email: str, password: str) -> dict[str, Any]:
    return request_json(
        "POST",
        f"{gateway_base}/auth/login",
        payload={"email": email, "password": password},
    )


def bearer_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def print_json(payload: Any) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))
