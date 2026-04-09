"""Enable Row-Level Security on core tables.

Revision ID: 0002_rls
Revises: 0001_initial
Create Date: 2026-04-07

Tables:
  - memory_records (postgres-init.sql)
  - strategies (postgres-init.sql)
  - crypto_decisions (crypto-agent)
  - order_events (order-service)
  - portfolio_positions (portfolio-service)

Policy: users may only SELECT/INSERT/UPDATE/DELETE rows where
        user_id = current_setting('app.current_user_id')::text

The ``admin`` role bypasses RLS entirely.
"""

from alembic import op

revision = "0002_rls"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

# Tables that carry a user_id column and should enforce RLS.
RLS_TABLES = [
    "memory_records",
    "strategies",
    "crypto_decisions",
    "order_events",
    "portfolio_positions",
]


def upgrade() -> None:
    for table in RLS_TABLES:
        # Idempotent: enable RLS (no-op if already enabled)
        op.execute(f"ALTER TABLE IF EXISTS {table} ENABLE ROW LEVEL SECURITY")

        # Force RLS even for table owners (except superusers)
        op.execute(f"ALTER TABLE IF EXISTS {table} FORCE ROW LEVEL SECURITY")

        # Drop existing policy to make migration re-runnable
        op.execute(
            f"DROP POLICY IF EXISTS rls_user_isolation ON {table}"
        )

        # Create policy: match user_id to session variable
        op.execute(f"""
            CREATE POLICY rls_user_isolation ON {table}
                USING (user_id = current_setting('app.current_user_id', true)::text)
                WITH CHECK (user_id = current_setting('app.current_user_id', true)::text)
        """)

    # Admin role bypasses RLS
    op.execute("DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'admin') THEN CREATE ROLE admin; END IF; END $$")
    for table in RLS_TABLES:
        op.execute(f"ALTER TABLE IF EXISTS {table} OWNER TO CURRENT_USER")
        op.execute(
            f"DROP POLICY IF EXISTS rls_admin_bypass ON {table}"
        )
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
