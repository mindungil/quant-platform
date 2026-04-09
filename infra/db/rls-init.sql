-- ---------------------------------------------------------------------------
-- Row-Level Security (RLS) for order / portfolio / credential tables
--
-- These tables are created at runtime by the services themselves, so we use
-- DO blocks that silently skip tables that don't exist yet.  When the tables
-- are eventually created and this script is re-run (or the service starts),
-- the policies will be applied.
--
-- The app sets current_setting('app.current_user_id') before queries.
-- current_setting(..., true) returns NULL when not set, which means the
-- USING clause evaluates to FALSE — secure by default (no rows returned).
--
-- The admin role (created in postgres-init.sql) bypasses RLS.
-- The postgres superuser automatically bypasses RLS.
-- ---------------------------------------------------------------------------

-- Ensure admin role exists (idempotent, matches postgres-init.sql)
DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'admin') THEN CREATE ROLE admin; END IF; END $$;

-- ---- order_events --------------------------------------------------------
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'order_events') THEN
    ALTER TABLE order_events ENABLE ROW LEVEL SECURITY;
    ALTER TABLE order_events FORCE ROW LEVEL SECURITY;
    EXECUTE 'DROP POLICY IF EXISTS rls_user_isolation ON order_events';
    EXECUTE 'DROP POLICY IF EXISTS rls_admin_bypass ON order_events';
  END IF;
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'order_events') THEN
    EXECUTE $policy$
      CREATE POLICY rls_user_isolation ON order_events
        USING (user_id = current_setting('app.current_user_id', true)::text)
        WITH CHECK (user_id = current_setting('app.current_user_id', true)::text)
    $policy$;
    EXECUTE $policy$
      CREATE POLICY rls_admin_bypass ON order_events TO admin USING (true) WITH CHECK (true)
    $policy$;
  END IF;
END $$;

-- ---- order_lifecycle_events ----------------------------------------------
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'order_lifecycle_events') THEN
    ALTER TABLE order_lifecycle_events ENABLE ROW LEVEL SECURITY;
    ALTER TABLE order_lifecycle_events FORCE ROW LEVEL SECURITY;
    EXECUTE 'DROP POLICY IF EXISTS rls_user_isolation ON order_lifecycle_events';
    EXECUTE 'DROP POLICY IF EXISTS rls_admin_bypass ON order_lifecycle_events';
  END IF;
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'order_lifecycle_events') THEN
    EXECUTE $policy$
      CREATE POLICY rls_user_isolation ON order_lifecycle_events
        USING (user_id = current_setting('app.current_user_id', true)::text)
        WITH CHECK (user_id = current_setting('app.current_user_id', true)::text)
    $policy$;
    EXECUTE $policy$
      CREATE POLICY rls_admin_bypass ON order_lifecycle_events TO admin USING (true) WITH CHECK (true)
    $policy$;
  END IF;
END $$;

-- ---- portfolio_positions -------------------------------------------------
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'portfolio_positions') THEN
    ALTER TABLE portfolio_positions ENABLE ROW LEVEL SECURITY;
    ALTER TABLE portfolio_positions FORCE ROW LEVEL SECURITY;
    EXECUTE 'DROP POLICY IF EXISTS rls_user_isolation ON portfolio_positions';
    EXECUTE 'DROP POLICY IF EXISTS rls_admin_bypass ON portfolio_positions';
  END IF;
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'portfolio_positions') THEN
    EXECUTE $policy$
      CREATE POLICY rls_user_isolation ON portfolio_positions
        USING (user_id = current_setting('app.current_user_id', true)::text)
        WITH CHECK (user_id = current_setting('app.current_user_id', true)::text)
    $policy$;
    EXECUTE $policy$
      CREATE POLICY rls_admin_bypass ON portfolio_positions TO admin USING (true) WITH CHECK (true)
    $policy$;
  END IF;
END $$;

-- ---- portfolio_fills -----------------------------------------------------
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'portfolio_fills') THEN
    ALTER TABLE portfolio_fills ENABLE ROW LEVEL SECURITY;
    ALTER TABLE portfolio_fills FORCE ROW LEVEL SECURITY;
    EXECUTE 'DROP POLICY IF EXISTS rls_user_isolation ON portfolio_fills';
    EXECUTE 'DROP POLICY IF EXISTS rls_admin_bypass ON portfolio_fills';
  END IF;
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'portfolio_fills') THEN
    EXECUTE $policy$
      CREATE POLICY rls_user_isolation ON portfolio_fills
        USING (user_id = current_setting('app.current_user_id', true)::text)
        WITH CHECK (user_id = current_setting('app.current_user_id', true)::text)
    $policy$;
    EXECUTE $policy$
      CREATE POLICY rls_admin_bypass ON portfolio_fills TO admin USING (true) WITH CHECK (true)
    $policy$;
  END IF;
END $$;

-- ---- credential_records --------------------------------------------------
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'credential_records') THEN
    ALTER TABLE credential_records ENABLE ROW LEVEL SECURITY;
    ALTER TABLE credential_records FORCE ROW LEVEL SECURITY;
    EXECUTE 'DROP POLICY IF EXISTS rls_user_isolation ON credential_records';
    EXECUTE 'DROP POLICY IF EXISTS rls_admin_bypass ON credential_records';
  END IF;
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'credential_records') THEN
    EXECUTE $policy$
      CREATE POLICY rls_user_isolation ON credential_records
        USING (user_id = current_setting('app.current_user_id', true)::text)
        WITH CHECK (user_id = current_setting('app.current_user_id', true)::text)
    $policy$;
    EXECUTE $policy$
      CREATE POLICY rls_admin_bypass ON credential_records TO admin USING (true) WITH CHECK (true)
    $policy$;
  END IF;
END $$;

-- ---- exchange_order_audits -----------------------------------------------
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'exchange_order_audits') THEN
    ALTER TABLE exchange_order_audits ENABLE ROW LEVEL SECURITY;
    ALTER TABLE exchange_order_audits FORCE ROW LEVEL SECURITY;
    EXECUTE 'DROP POLICY IF EXISTS rls_user_isolation ON exchange_order_audits';
    EXECUTE 'DROP POLICY IF EXISTS rls_admin_bypass ON exchange_order_audits';
  END IF;
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'exchange_order_audits') THEN
    EXECUTE $policy$
      CREATE POLICY rls_user_isolation ON exchange_order_audits
        USING (user_id = current_setting('app.current_user_id', true)::text)
        WITH CHECK (user_id = current_setting('app.current_user_id', true)::text)
    $policy$;
    EXECUTE $policy$
      CREATE POLICY rls_admin_bypass ON exchange_order_audits TO admin USING (true) WITH CHECK (true)
    $policy$;
  END IF;
END $$;
