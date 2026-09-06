-- =====================================================================
-- campaign-ledger / 001_schema.sql
-- Schema, enums, tables, indexes for the DoviLoop Teams campaign ledger.
--
-- TARGET PROJECT: oqpeebtwtikdzorgouxd  (the lead-pipeline / DSD project)
-- TARGET SCHEMA:  campaign               (new schema, created below)
--
-- The target project ref above was CONFIRMED by Dovy on 2026-09-06 (BLOCKED.md
-- B-1, resolved). Still check the project ref in the URL bar before running.
--
-- This must NEVER be run against the PRODUCT database (the Frankfurt project).
-- Check the project ref in the URL bar against the one in .env.example first.
-- The product ref is deliberately not written anywhere in these .sql files, so
-- that no copy-paste from a migration can ever aim at it.
--
-- Run order: 001_schema.sql -> 002_rls.sql -> 003_views.sql
-- Re-runnable: yes. Every statement is IF NOT EXISTS / guarded.
-- =====================================================================

create schema if not exists campaign;

-- ---------------------------------------------------------------------
-- Enums
--
-- Written as guarded DO blocks so the file can be run twice without error
-- (CREATE TYPE has no IF NOT EXISTS before PG 16).
-- ---------------------------------------------------------------------

do $$ begin
  create type campaign.market as enum ('dk', 'lt', 'global');
exception when duplicate_object then null; end $$;

do $$ begin
  create type campaign.segment as enum ('accounting', 'insurance', 'admin');
exception when duplicate_object then null; end $$;

do $$ begin
  create type campaign.company_source as enum ('icp_finder', 'linkedin', 'inbound');
exception when duplicate_object then null; end $$;

do $$ begin
  create type campaign.email_source as enum ('instantly_finder', 'public', 'inbound');
exception when duplicate_object then null; end $$;

do $$ begin
  create type campaign.touch_channel as enum ('linkedin_connect', 'linkedin_dm', 'email', 'phone');
exception when duplicate_object then null; end $$;

do $$ begin
  create type campaign.lang as enum ('en', 'da', 'lt');
exception when duplicate_object then null; end $$;

-- The six-value reply taxonomy, canonical campaign-wide since 2026-09-06 and
-- identical to the set the outreach engine's classifier emits. Do not extend
-- without changing that repo's classifier in the same change.
--
-- How 003_views.sql reads them: interested and referred are POSITIVE; not_now
-- and objection are NEUTRAL (a reply, but not a positive one); not_a_fit and
-- unsubscribe are NEGATIVE. NULL means no reply yet, not a seventh class.
do $$ begin
  create type campaign.reply_sentiment as enum (
    'interested', 'not_now', 'not_a_fit', 'referred', 'objection', 'unsubscribe'
  );
exception when duplicate_object then null; end $$;

-- Qualifier payload enums. These four mirror the shared contract in
-- 00-START-HERE.md exactly. Changing a label here breaks Batch A's form and
-- Batch F's routing.
do $$ begin
  create type campaign.team_size as enum ('1-9', '10-24', '25-49', '50+');
exception when duplicate_object then null; end $$;

do $$ begin
  create type campaign.email_client as enum ('outlook', 'gmail', 'other');
exception when duplicate_object then null; end $$;

do $$ begin
  create type campaign.lead_role as enum ('owner_partner', 'ops_office_manager', 'it_admin', 'other');
exception when duplicate_object then null; end $$;

do $$ begin
  create type campaign.lead_source as enum ('reel', 'ad', 'outreach', 'direct');
exception when duplicate_object then null; end $$;

do $$ begin
  create type campaign.lead_stage as enum ('qualified', 'gmail_on_request', 'too_small');
exception when duplicate_object then null; end $$;

do $$ begin
  create type campaign.meeting_status as enum ('booked', 'held', 'no_show', 'cancelled');
exception when duplicate_object then null; end $$;

do $$ begin
  create type campaign.content_kind as enum ('reel', 'static_ad', 'video_ad');
exception when duplicate_object then null; end $$;

do $$ begin
  create type campaign.content_lane as enum ('higgsfield', 'hyperframes', 'on_camera', 'static');
exception when duplicate_object then null; end $$;

-- ---------------------------------------------------------------------
-- companies - one row per target firm
--
-- domain is the natural key every upsert de-duplicates on, so it is NOT NULL
-- and forced lowercase. A nullable unique column would silently let duplicates
-- in: Postgres allows unlimited NULLs in a unique index.
--
-- dsd_company_id is a deliberate loose end. The DSD tables live in this same
-- project but their shape could not be read during the build, so there is no
-- REFERENCES clause. See AUDIT.md section 2.
-- ---------------------------------------------------------------------

create table if not exists campaign.companies (
  id             uuid primary key default gen_random_uuid(),
  name           text not null,
  domain         text not null unique,
  market         campaign.market not null,
  segment        campaign.segment,
  country        text,
  est_size       integer,
  uses_m365      boolean,
  has_dev_team   boolean,
  fit_score      integer,
  hook_seed      text,
  source         campaign.company_source not null default 'icp_finder',
  dsd_company_id uuid,
  created_at     timestamptz not null default now(),
  constraint companies_domain_lowercase check (domain = lower(domain)),
  constraint companies_domain_shape     check (domain like '%.%' and domain not like '%@%'),
  constraint companies_fit_score_range  check (fit_score is null or (fit_score >= 0 and fit_score <= 100)),
  constraint companies_est_size_sane    check (est_size is null or est_size >= 0)
);

create index if not exists companies_market_idx  on campaign.companies (market);
create index if not exists companies_segment_idx on campaign.companies (segment);
create index if not exists companies_dsd_idx     on campaign.companies (dsd_company_id);

comment on table campaign.companies is
  'Target firms. Natural key: domain (lowercase, unique).';
comment on column campaign.companies.dsd_company_id is
  'Nullable link to the DSD discovery tables. No FK yet - the DSD table shape was not readable during the build. See BLOCKED.md B-3.';
comment on column campaign.companies.uses_m365 is
  'From an MX lookup. The strong ICP signal.';
comment on column campaign.companies.has_dev_team is
  'Best effort. TRUE disqualifies: the hard ICP requirement is no in-house dev team.';

-- ---------------------------------------------------------------------
-- contacts - people at those firms
--
-- natural_key is written by the client: the LinkedIn URL if there is one, else
-- the email, else the lowercased name. It exists as a plain column rather than
-- an expression index so PostgREST can name it as an ON CONFLICT target.
-- ---------------------------------------------------------------------

create table if not exists campaign.contacts (
  id           uuid primary key default gen_random_uuid(),
  company_id   uuid not null references campaign.companies (id) on delete cascade,
  full_name    text,
  role_guess   text,
  linkedin_url text,
  email        text,
  email_source campaign.email_source,
  market       campaign.market not null,
  natural_key  text not null,
  created_at   timestamptz not null default now(),
  constraint contacts_identifiable check (
    coalesce(linkedin_url, '') <> '' or coalesce(email, '') <> '' or coalesce(full_name, '') <> ''
  ),
  constraint contacts_company_natural_key unique (company_id, natural_key)
);

create index if not exists contacts_company_id_idx on campaign.contacts (company_id);
create index if not exists contacts_market_idx     on campaign.contacts (market);
create index if not exists contacts_email_idx      on campaign.contacts (email);

comment on column campaign.contacts.natural_key is
  'linkedin_url, else email, else lowercased full_name. Client-computed. Makes upsert_contact idempotent across re-runs.';

-- ---------------------------------------------------------------------
-- touches - every outbound action, one row each
--
-- Natural key is (contact_id, channel, sequence_step): step 2 of the LinkedIn
-- DM sequence to one person is one row, however many times the engine re-runs.
--
-- No Danish addresses may enter the email channel until Dovy confirms the
-- Danish marketing-law position. That rule is enforced in the outreach engine,
-- not here - this table records what happened, it does not police it.
-- ---------------------------------------------------------------------

create table if not exists campaign.touches (
  id              uuid primary key default gen_random_uuid(),
  contact_id      uuid not null references campaign.contacts (id) on delete cascade,
  channel         campaign.touch_channel not null,
  sequence_step   integer not null default 1,
  language        campaign.lang not null,
  sent_at         timestamptz not null default now(),
  replied_at      timestamptz,
  reply_sentiment campaign.reply_sentiment,
  notes           text,
  constraint touches_step_positive check (sequence_step >= 1),
  constraint touches_sentiment_needs_reply check (
    reply_sentiment is null or replied_at is not null
  ),
  constraint touches_contact_channel_step unique (contact_id, channel, sequence_step)
);

create index if not exists touches_contact_id_idx on campaign.touches (contact_id);
create index if not exists touches_channel_idx    on campaign.touches (channel);
create index if not exists touches_sent_at_idx    on campaign.touches (sent_at);
create index if not exists touches_replied_at_idx on campaign.touches (replied_at);

comment on column campaign.touches.reply_sentiment is
  'The six-value taxonomy shared with the outreach engine: interested, not_now, not_a_fit, referred, objection, unsubscribe. NULL means no reply yet, not a seventh class.';

-- ---------------------------------------------------------------------
-- leads - qualifier submissions, one row per form submit
--
-- The column list and the three stage values are the fixed contract between
-- Batch A's form, this table and Batch F's routing. Do not rename.
--
-- natural_key = lower(work_email) || '|' || submitted_at. A person submitting
-- twice on different days gets two rows (correct); an n8n retry of the same
-- submission gets one (also correct).
-- ---------------------------------------------------------------------

create table if not exists campaign.leads (
  id           uuid primary key default gen_random_uuid(),
  company_name text not null,
  work_email   text not null,
  phone        text,
  team_size    campaign.team_size not null,
  email_client campaign.email_client not null,
  role         campaign.lead_role,
  market       campaign.market not null,
  locale       campaign.lang not null,
  source       campaign.lead_source not null,
  utm_source   text,
  utm_medium   text,
  utm_campaign text,
  utm_content  text,
  stage        campaign.lead_stage not null,
  company_id   uuid references campaign.companies (id) on delete set null,
  submitted_at timestamptz not null,
  natural_key  text not null unique,
  -- Half of the routing table, enforced. A 1-9 team must never be given a
  -- booking link, and nobody bigger belongs in the nurture list. The
  -- qualified / gmail_on_request split is deliberately NOT constrained so a
  -- human can upgrade a lead by hand after a Gmail firm agrees to move.
  constraint leads_too_small_matches_size check (
    (team_size = '1-9') = (stage = 'too_small')
  )
);

create index if not exists leads_work_email_idx   on campaign.leads (work_email);
create index if not exists leads_company_id_idx   on campaign.leads (company_id);
create index if not exists leads_market_idx       on campaign.leads (market);
create index if not exists leads_source_idx       on campaign.leads (source);
create index if not exists leads_stage_idx        on campaign.leads (stage);
create index if not exists leads_submitted_at_idx on campaign.leads (submitted_at);

comment on table campaign.leads is
  'Qualifier submissions. Shape fixed by the shared contract in 00-START-HERE.md.';
comment on column campaign.leads.stage is
  'qualified = 10+ seats on Outlook. gmail_on_request = 10+ seats on Gmail/other. too_small = 1-9 seats, redirected to doviloop.dev pricing and a 3-email nurture.';

-- ---------------------------------------------------------------------
-- meetings - discovery calls
-- ---------------------------------------------------------------------

create table if not exists campaign.meetings (
  id            uuid primary key default gen_random_uuid(),
  lead_id       uuid not null references campaign.leads (id) on delete cascade,
  scheduled_for timestamptz not null,
  status        campaign.meeting_status not null default 'booked',
  outcome_notes text,
  pilot_agreed  boolean not null default false,
  constraint meetings_lead_slot unique (lead_id, scheduled_for),
  constraint meetings_agreement_needs_held check (
    pilot_agreed = false or status = 'held'
  )
);

create index if not exists meetings_lead_id_idx       on campaign.meetings (lead_id);
create index if not exists meetings_status_idx        on campaign.meetings (status);
create index if not exists meetings_scheduled_for_idx on campaign.meetings (scheduled_for);

-- ---------------------------------------------------------------------
-- pilots - the free two weeks
--
-- charge_due_on is generated, never written by hand, so day 14 cannot drift
-- from what Stripe was told.
-- ---------------------------------------------------------------------

create table if not exists campaign.pilots (
  id                 uuid primary key default gen_random_uuid(),
  lead_id            uuid not null unique references campaign.leads (id) on delete cascade,
  started_on         date not null,
  workshop_done_on   date,
  setup_done_on      date,
  seats              integer not null,
  charge_due_on      date generated always as (started_on + 14) stored,
  converted          boolean not null default false,
  stripe_customer_id text,
  mrr_eur            numeric(10,2),
  lost_reason        text,
  constraint pilots_seats_minimum check (seats >= 10),
  constraint pilots_mrr_needs_conversion check (
    (converted = true) or mrr_eur is null
  ),
  constraint pilots_lost_reason_only_when_lost check (
    (converted = false) or lost_reason is null
  )
);

create index if not exists pilots_lead_id_idx       on campaign.pilots (lead_id);
create index if not exists pilots_started_on_idx    on campaign.pilots (started_on);
create index if not exists pilots_charge_due_on_idx on campaign.pilots (charge_due_on);

comment on column campaign.pilots.seats is
  'Minimum 10. The offer does not exist below that.';
comment on column campaign.pilots.mrr_eur is
  'Recurring revenue. NOTE: the offer is priced in USD ($89 per seat per month plus a $500 one-off setup, which is not MRR) but this column is EUR, per spec. Whoever writes it must convert. See RUN-REPORT.md.';

-- ---------------------------------------------------------------------
-- content - one row per reel or ad creative
--
-- natural_key = kind:lane:language:slug(hook). The reel engine regenerates
-- the same week's scripts on re-run and must not fan out duplicate rows.
-- ---------------------------------------------------------------------

create table if not exists campaign.content (
  id           uuid primary key default gen_random_uuid(),
  kind         campaign.content_kind not null,
  lane         campaign.content_lane not null,
  language     campaign.lang not null,
  hook         text not null,
  script_path  text,
  asset_path   text,
  platforms    text[] not null default '{}',
  published_at timestamptz,
  buffer_id    text,
  natural_key  text not null unique,
  created_at   timestamptz not null default now()
);

create index if not exists content_kind_idx         on campaign.content (kind);
create index if not exists content_lane_idx         on campaign.content (lane);
create index if not exists content_language_idx     on campaign.content (language);
create index if not exists content_published_at_idx on campaign.content (published_at);

comment on column campaign.content.platforms is
  'Where it went out: linkedin, youtube_shorts, instagram, facebook.';

-- ---------------------------------------------------------------------
-- content_stats - daily snapshot per content row, per platform
-- ---------------------------------------------------------------------

create table if not exists campaign.content_stats (
  id          uuid primary key default gen_random_uuid(),
  content_id  uuid not null references campaign.content (id) on delete cascade,
  platform    text not null,
  captured_on date not null,
  views       integer not null default 0,
  likes       integer not null default 0,
  comments    integer not null default 0,
  saves       integer not null default 0,
  clicks      integer not null default 0,
  constraint content_stats_snapshot unique (content_id, platform, captured_on),
  constraint content_stats_non_negative check (
    views >= 0 and likes >= 0 and comments >= 0 and saves >= 0 and clicks >= 0
  )
);

create index if not exists content_stats_content_id_idx  on campaign.content_stats (content_id);
create index if not exists content_stats_captured_on_idx on campaign.content_stats (captured_on);

-- ---------------------------------------------------------------------
-- ad_stats - daily snapshot per ad set
--
-- creative_content_id is nullable: Meta reports at ad-set level and the link
-- back to a specific creative is not always recoverable.
-- ---------------------------------------------------------------------

create table if not exists campaign.ad_stats (
  id                  uuid primary key default gen_random_uuid(),
  campaign_name       text not null,
  ad_set_name         text not null,
  creative_content_id uuid references campaign.content (id) on delete set null,
  captured_on         date not null,
  spend_eur           numeric(10,2) not null default 0,
  impressions         integer not null default 0,
  clicks              integer not null default 0,
  leads               integer not null default 0,
  constraint ad_stats_snapshot unique (campaign_name, ad_set_name, captured_on),
  constraint ad_stats_non_negative check (
    spend_eur >= 0 and impressions >= 0 and clicks >= 0 and leads >= 0
  )
);

create index if not exists ad_stats_creative_idx    on campaign.ad_stats (creative_content_id);
create index if not exists ad_stats_captured_on_idx on campaign.ad_stats (captured_on);

comment on column campaign.ad_stats.spend_eur is
  'Meta retargeting is capped at EUR 500/month total across all ad sets.';
