"""Row-Level Security (RLS) helpers.

Sets the ``app.current_user_id`` session variable so that Postgres RLS
policies can restrict rows to the authenticated user.

Usage::

    from shared.rls import set_rls_user

    with sql_store.connection() as conn:
        set_rls_user(conn, user_id)
        conn.execute(text("SELECT * FROM orders"))
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Connection


def set_rls_user(conn: Connection, user_id: str) -> None:
    """Set ``app.current_user_id`` for the current transaction.

    This must be called inside an open transaction before any queries
    that rely on RLS policies.
    """
    conn.execute(
        text("SET LOCAL app.current_user_id = :uid"),
        {"uid": user_id},
    )
