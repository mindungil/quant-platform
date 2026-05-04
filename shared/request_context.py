from __future__ import annotations

from contextvars import ContextVar, Token


_request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
_correlation_id_var: ContextVar[str | None] = ContextVar("correlation_id", default=None)
_user_id_var: ContextVar[str | None] = ContextVar("user_id", default=None)


def set_request_context(
    *,
    request_id: str | None,
    correlation_id: str | None,
    user_id: str | None = None,
) -> tuple[Token, Token, Token]:
    return (
        _request_id_var.set(request_id),
        _correlation_id_var.set(correlation_id),
        _user_id_var.set(user_id),
    )


def reset_request_context(tokens: tuple[Token, Token, Token]) -> None:
    request_token, correlation_token, user_token = tokens
    _request_id_var.reset(request_token)
    _correlation_id_var.reset(correlation_token)
    _user_id_var.reset(user_token)


def current_request_id() -> str | None:
    return _request_id_var.get()


def current_correlation_id() -> str | None:
    return _correlation_id_var.get()


def current_user_id() -> str | None:
    return _user_id_var.get()


def current_request_headers() -> dict[str, str]:
    headers: dict[str, str] = {}
    request_id = current_request_id()
    correlation_id = current_correlation_id()
    user_id = current_user_id()
    if request_id is not None:
        headers["X-Request-ID"] = request_id
    if correlation_id is not None:
        headers["X-Correlation-ID"] = correlation_id
    if user_id is not None:
        headers["X-User-ID"] = user_id
    return headers
