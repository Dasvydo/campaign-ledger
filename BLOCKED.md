# BLOCKED - Batch B (`campaign-ledger`)

Logged and continued past, per global rule 5. Nothing here stopped the batch.

Status on 2026-09-06: **B-1 and B-4 resolved** by Dovy's decisions (see
`RUN-REPORT.md`, "Decisions applied"). B-2, B-3, B-5 and B-6 remain open.

---

## B-1 - Target Supabase project could not be confirmed - RESOLVED 2026-09-06

**Resolution:** Dovy confirmed the ledger lives in Supabase project
`oqpeebtwtikdzorgouxd`, schema `campaign`. The migrations were already written
against that project; the "confirm before running" warnings in the migration
headers, `README.md` and `.env.example` now state it as confirmed. Nothing else
changed. The migrations are still Dovy's to run (global rule 3).

The original entry, for the record:

**Missing:** any way to confirm that project `oqpeebtwtikdzorgouxd` exists, is
Dovy's, and is the one holding the DSD LinkedIn discovery data. No `supabase/`
directory on the container, no `.env` in this repo, no `supabase-architect` skill
notes, and `outreach-engine` is out of bounds for this session.

**What I did:** wrote all three migrations against `oqpeebtwtikdzorgouxd`, schema
`campaign`, exactly as the spec names it. Did **not** guess an alternative and did
**not** fall back to the product project.

---

## B-2 - Session credentials point at the FORBIDDEN product database

**What happened:** this container's `SUPABASE_URL` resolves to project ref
`kngcxwcybozgqgnoweyt` - the product database (Frankfurt), the hard boundary. A
matching `SUPABASE_SERVICE_ROLE_KEY` is set alongside it.

**What I did:** never connected. Additionally, `src/campaign_db.py`
(a) reads `SUPABASE_SERVICE_KEY`, **not** the ambient `SUPABASE_SERVICE_ROLE_KEY`,
so the product key cannot be picked up by accident, and (b) raises at startup if
`SUPABASE_URL` contains `kngcxwcybozgqgnoweyt`.

**Blocks:** nothing in this batch. It is a standing hazard for **Batches C, D, E
and F**, which import this client - if any of them runs in a container with these
same ambient variables and someone relaxes the guard, outreach data lands in the
product database.

**To unblock (Dovy, ~2 min):** when copying the ledger service key into the other
repos' `.env` files, set `SUPABASE_URL` to the **ledger** project in each one, and
leave the guard in place.

---

## B-3 - DSD table structure unreadable, so no foreign key to it

**Missing:** the DSD tables' names, columns and primary-key types. They live in a
project this session cannot (and must not) connect to, and their DDL, if checked
in anywhere, is in `outreach-engine`, which is out of bounds.

**What I did:** added a nullable, unconstrained `campaign.companies.dsd_company_id
uuid`. No `REFERENCES` clause - an invented reference would have made
`001_schema.sql` fail on execution.

**Blocks:** automatic de-duplication of campaign companies against companies DSD
already discovered. Today the campaign ledger will re-create a row for a company
DSD already has, linked only by `domain`.

**To unblock (Dovy, ~5 min, optional and safely deferrable):** confirm the DSD
company table name and PK type, then a short `004_dsd_link.sql` can add the
foreign key and a backfill on `domain`. Not needed for the campaign to run.

---

## B-4 - Six-value taxonomy could not be diffed against the live classifier - RESOLVED 2026-09-06

**Resolution:** the spec's six values and the outreach engine's six values did
differ, and Dovy resolved it campaign-wide in favour of the outreach engine's
set: `interested`, `not_now`, `not_a_fit`, `referred`, `objection`,
`unsubscribe`. The enum in `001_schema.sql`, the client's validation list, the
seed and the views now use exactly those. The retired set is recorded once, in
`RUN-REPORT.md` under "Decisions applied".

The original entry, for the record:

**Missing:** sight of the actual string values `outreach-engine` writes. The six
values were taken verbatim from the spec and made a Postgres enum, which will
**reject** any value outside that set.

**Blocks:** nothing yet. It becomes a silent write failure in Batch C if that
repo's classifier uses different casing or spelling.

---

## B-5 - Schema `campaign` is not exposed to PostgREST by default

**What is missing:** Supabase's REST API only serves schemas listed under
Settings → API → *Exposed schemas* (default: `public`, `graphql_public`).
`src/campaign_db.py` talks to PostgREST with `Accept-Profile: campaign` /
`Content-Profile: campaign`, which returns `PGRST106 (schema not in search path)`
until `campaign` is added to that list.

I cannot add it - that is a dashboard setting on a project I must not connect to.

**Blocks:** every write from Batches C, D and E, and the Friday brief, **against
a live database**. Local proof runs are unaffected.

**To unblock (Dovy, ~1 min):** Supabase dashboard → Settings → API → Exposed
schemas → add `campaign` → save. This is step 3 in `README.md`.

---

## B-6 - No live database was available to test against

**Missing:** any reachable Postgres. `psql` is installed but there is no local
server (`initdb`/`pg_ctl` are absent), egress to Supabase is blocked, and
connecting is forbidden anyway.

**What I did:** validated all three migrations by parsing them with `sqlglot`
(Postgres dialect) and exercised the schema, the seed, the client's idempotency
and the three views end to end against a local SQLite mirror built by
transpiling the committed `.sql` files. Method and its limits are written up in
`RUN-REPORT.md`.

**Blocks:** final confirmation that the Postgres views return exactly the numbers
the SQLite mirror returned, and that RLS behaves as written.

**To unblock (Dovy, ~3 min):** after running the migrations, run
`select * from campaign.v_market_funnel;` and the two other views, then run
`seed/seed_demo.py` against the real project and re-run `src/friday_brief.py`.
