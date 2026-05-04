from __future__ import annotations

import hmac
import time
from hashlib import sha256

from fastapi import HTTPException, Request


def build_internal_admin_headers(secret: str, actor_user_id: str, path: str) -> dict[str, str]:
    timestamp = str(int(time.time()))
    message = f"{actor_user_id}:{timestamp}:{path}"
    signature = hmac.new(
        secret.encode("utf-8"),
        message.encode("utf-8"),
        sha256,
    ).hexdigest()
    return {
        "X-Internal-Actor-User-ID": actor_user_id,
        "X-Internal-Admin-Timestamp": timestamp,
        "X-Internal-Admin-Signature": signature,
    }


def verify_internal_admin_headers(
    *,
    secret: str,
    path: str,
    actor_user_id: str | None,
    timestamp: str | None,
    signature: str | None,
    ttl_seconds: int = 300,
) -> str | None:
    if not actor_user_id or not timestamp or not signature:
        return None
    try:
        parsed = int(timestamp)
    except ValueError:
        return None
    if abs(int(time.time()) - parsed) > ttl_seconds:
        return None
    message = f"{actor_user_id}:{timestamp}:{path}"
    expected = hmac.new(
        secret.encode("utf-8"),
        message.encode("utf-8"),
        sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return None
    return actor_user_id


def require_internal_admin(
    *,
    request: Request,
    secret: str,
    actor_user_id: str | None,
    timestamp: str | None,
    signature: str | None,
    ttl_seconds: int = 300,
) -> str:
    verified = verify_internal_admin_headers(
        secret=secret,
        path=request.url.path,
        actor_user_id=actor_user_id,
        timestamp=timestamp,
        signature=signature,
        ttl_seconds=ttl_seconds,
    )
    if verified is None:
        if not actor_user_id or not timestamp or not signature:
            raise HTTPException(status_code=403, detail="missing_internal_admin_headers")
        try:
            int(timestamp)
        except ValueError as exc:
            raise HTTPException(status_code=403, detail="invalid_internal_admin_timestamp") from exc
        if abs(int(time.time()) - int(timestamp)) > ttl_seconds:
            raise HTTPException(status_code=403, detail="expired_internal_admin_signature")
        raise HTTPException(status_code=403, detail="invalid_internal_admin_signature")
    return verified
