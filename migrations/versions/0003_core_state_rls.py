"""Expand Row-Level Security coverage for core stateful tables.

Revision ID: 0003_core_state_rls
Revises: 0002_rls
Create Date: 2026-04-22
"""

from alembic import op

revision = "0003_core_state_rls"
down_revision = "0002_rls"
branch_labels = None
depends_on = None

RLS_TABLES = [
    "memory_records",
    "strategy_records",
    "order_events",
    "order_lifecycle_events",
    "portfolio_positions",
    "portfolio_fills",
    "statistics_trades",
    "credential_records",
    "credential_audit_log",
]


def upgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'admin') THEN CREATE ROLE admin; END IF; END $$"
    )
    for table in RLS_TABLES:
        op.execute(f"ALTER TABLE IF EXISTS {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE IF EXISTS {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS rls_user_isolation ON {table}")
        op.execute(f"""
            CREATE POLICY rls_user_isolation ON {table}
                USING (user_id = current_setting('app.current_user_id', true)::text)
                WITH CHECK (user_id = current_setting('app.current_user_id', true)::text)
        """)
        op.execute(f"DROP POLICY IF EXISTS rls_admin_bypass ON {table}")
        op.execute(f"""
            CREATE POLICY rls_admin_bypass ON {table}
                TO admin
                USING (true)
                WITH CHECK (true)
        """)


def downgrade() -> None:
    for table in RLS_TABLES:
        op.execute(f"DROP POLICY IF EXISTS rls_admin_bypass ON {table}")
        op.execute(f"DROP POLICY IF EXISTS rls_user_isolation ON {table}")
        op.execute(f"ALTER TABLE IF EXISTS {table} DISABLE ROW LEVEL SECURITY")
