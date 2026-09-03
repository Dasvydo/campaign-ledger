-- =====================================================================
-- campaign-ledger / 002_rls.sql
-- Row level security. Service role only. No anon policy, anywhere.
--
-- TARGET PROJECT: oqpeebtwtikdzorgouxd   (confirm before running - see B-1)
-- Run order: 001_schema.sql -> 002_rls.sql -> 003_views.sql
-- Re-runnable: yes.
--
-- Why this file is short and has no CREATE POLICY in it
-- -----------------------------------------------------
-- This schema holds names, work emails, phone numbers and LinkedIn profiles of
-- real people who never asked to be contacted. It must not be readable from a
-- browser, ever.
--
-- In Supabase, `service_role` carries the BYPASSRLS attribute. So the correct
-- shape for "service role only" is: turn RLS ON and write NO policies at all.
-- With RLS on and zero policies, every `anon` and `authenticated` request
-- returns an empty set or a permission error, while server-side code holding
-- the service key keeps full access.
--
-- Adding a policy here - even one that looks restrictive - would be a mistake:
-- policies only ever GRANT. The absence of policies is the security control.
--
-- The REVOKEs below are the second layer. RLS alone still lets a browser client
-- discover the table exists; revoking schema USAGE from anon and authenticated
-- means PostgREST will not expose these tables to them at all.
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. RLS on, on every table in the schema
-- ---------------------------------------------------------------------

alter table campaign.companies     enable row level security;
alter table campaign.contacts      enable row level security;
alter table campaign.touches       enable row level security;
alter table campaign.leads         enable row level security;
alter table campaign.meetings      enable row level security;
alter table campaign.pilots        enable row level security;
alter table campaign.content       enable row level security;
alter table campaign.content_stats enable row level security;
alter table campaign.ad_stats      enable row level security;

-- Catch-all: if a later migration adds a table and forgets the line above,
-- re-running this file switches RLS on for it too.
do $$
declare t record;
begin
  for t in
    select tablename
    from pg_tables
    where schemaname = 'campaign'
  loop
    execute format('alter table campaign.%I enable row level security', t.tablename);
  end loop;
end $$;

-- ---------------------------------------------------------------------
-- 2. Nothing reaches the browser roles
-- ---------------------------------------------------------------------

revoke all on schema campaign from anon, authenticated;
revoke all on all tables    in schema campaign from anon, authenticated;
revoke all on all sequences in schema campaign from anon, authenticated;
revoke all on all functions in schema campaign from anon, authenticated;

-- And for anything created later.
alter default privileges in schema campaign revoke all on tables    from anon, authenticated;
alter default privileges in schema campaign revoke all on sequences from anon, authenticated;
alter default privileges in schema campaign revoke all on functions from anon, authenticated;

-- ---------------------------------------------------------------------
-- 3. The service role keeps full access
--
-- service_role normally inherits these from Supabase's own defaults. Granting
-- explicitly makes this file self-contained, so the ledger keeps working if the
-- project's default privileges are ever tightened.
-- ---------------------------------------------------------------------

grant usage on schema campaign to service_role;
grant all on all tables    in schema campaign to service_role;
grant all on all sequences in schema campaign to service_role;

alter default privileges in schema campaign grant all on tables    to service_role;
alter default privileges in schema campaign grant all on sequences to service_role;

-- ---------------------------------------------------------------------
-- 4. Verify, by hand, after running this
--
-- (a) Every table should come back with rowsecurity = true:
--
--   select tablename, rowsecurity
--   from pg_tables
--   where schemaname = 'campaign'
--   order by tablename;
--
-- (b) This MUST return zero rows. One row here means someone opened the
--     outreach list to the internet:
--
--   select schemaname, tablename, policyname, roles
--   from pg_policies
--   where schemaname = 'campaign';
--
-- (c) This must also return zero rows - no browser role holds any grant:
--
--   select grantee, table_name, privilege_type
--   from information_schema.role_table_grants
--   where table_schema = 'campaign'
--     and grantee in ('anon', 'authenticated');
-- ---------------------------------------------------------------------
