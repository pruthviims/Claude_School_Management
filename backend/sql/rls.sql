-- Postgres row-level security. Apply AFTER the first migration.
--
-- This is the real tenant boundary for the SaaS phase. The Django
-- TenantMiddleware sets app.school_id per request; these policies make a
-- cross-tenant query impossible even if application code forgets a filter.
--
-- Run as superuser, then have the app connect as a NON-superuser role:
-- superusers and table owners bypass RLS unless FORCE is set.

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'fees_academicyear', 'fees_classlevel', 'fees_section', 'fees_stream',
    'fees_feehead', 'fees_feestructure', 'fees_student', 'fees_enrollment',
    'fees_charge', 'fees_concession', 'fees_invoice', 'fees_payment',
    'fees_allocation', 'fees_promotionbatch', 'fees_documentcounter',
    'fees_consentnotice', 'fees_consentrecord'
  ]
  LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format($f$
      CREATE POLICY tenant_isolation ON %I
      USING (school_id = current_setting('app.school_id', true)::uuid)
      WITH CHECK (school_id = current_setting('app.school_id', true)::uuid)
    $f$, t);
  END LOOP;
END $$;

-- Application role that RLS actually applies to.
-- CREATE ROLE schoolfees_app LOGIN PASSWORD 'xxx';
-- GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO schoolfees_app;
