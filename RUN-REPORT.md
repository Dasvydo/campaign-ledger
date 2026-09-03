# RUN-REPORT - Batch B (`campaign-ledger`)

Branch `campaign/b-ledger`. Three commits, nothing pushed, no remote configured.
No database was connected to at any point, and no migration was applied.

---

## What was built

| File | What it is |
|---|---|
| `migrations/001_schema.sql` | schema `campaign`, 15 enums, 9 tables, indexes on every FK, on `companies.domain`, on `leads.work_email` and on both `captured_on` columns |
| `migrations/002_rls.sql` | RLS enabled on all 9 tables, zero policies, `anon`/`authenticated` revoked from the schema and from future objects |
| `migrations/003_views.sql` | `v_market_funnel`, `v_channel_funnel`, `v_content_perf` |
| `src/campaign_db.py` | the eleven agreed functions, zero dependencies, idempotent on natural keys |
| `src/friday_brief.py` | renders `briefs/YYYY-MM-DD.md` and prints to stdout |
| `seed/seed_demo.py` | ~90 deterministic rows across every table |
| `.env.example` | the two variables, empty |
| `README.md` | setup in four steps, the full client signature list, the idempotency table |
| `AUDIT.md` | Phase 0, written before any other file changed |
| `BLOCKED.md` | six open items, B-1 to B-6 |
| `tools/validate_sql.py` | offline parse-check |
| `tests/sqlite_mirror.py` | runs the committed `.sql` on in-memory SQLite |
| `tests/run_local_proof.py` | the QA gate as code: 42 checks, all passing |
| `briefs/2026-09-25.md` | an example brief from seed data, labelled as such in its first line |

Three design decisions worth surfacing, because they are the ones that would be
expensive to change later:

**The views compute each metric as its own aggregate CTE, joined once at the
end.** The obvious alternative, one chain of LEFT JOINs from companies down to
pilots, multiplies rows at every one-to-many step: a firm with four contacts
counts as four companies. These are the numbers a market gets killed on, so they
are computed independently. `tests/run_local_proof.py` asserts the four-firms-per
-market figure specifically to catch a regression here.

**`cost_per_booked_call_eur` is NULL for channels with no spend, not 0.00.**
"Outreach costs EUR 0.00 per booked call" is a lie that would make ads look
infinitely worse than a channel that costs a week of Dovy's time.

**Facts about the past are insert-if-absent; facts that get enriched are merged.**
A replayed `log_touch` returns the original row rather than moving `sent_at` to
today. Re-dating a send would corrupt every reply-rate number in the brief. There
is a check for this.

---

## The QA gate, line by line

Run `python3 tests/run_local_proof.py` to reproduce. 42 checks, 0 failures.

### [x] Migrations parse (validate SQL syntax locally; do not connect)

**How, exactly.** Two independent passes, neither of which opens a socket.

1. `tools/validate_sql.py` parses all three files with **sqlglot 30.18.0**,
   Postgres dialect (`sqlglot.parse(sql, read="postgres")`). Result: **67
   statements parsed into a full AST, 35 returned as opaque `Command` nodes.**

   The honest limit: sqlglot does not model PL/pgSQL `DO $$ ... $$` blocks,
   `ALTER TABLE ... ENABLE ROW LEVEL SECURITY`, or `GRANT`/`REVOKE ... ON ALL
   ... IN SCHEMA`. Those 35 statements - the 15 guarded `CREATE TYPE` enums, the
   RLS loop, and every privilege statement - passed through **unchecked**. The
   validator prints and counts them rather than quietly claiming a clean bill.
   Every `CREATE TABLE`, `CREATE VIEW`, `CREATE INDEX` and constraint was fully
   parsed, and all 12 statements in `003_views.sql` parsed completely.

2. `tests/sqlite_mirror.py` mechanically rewrites the committed `001` and `003`
   into SQLite and **executes** them. This is the stronger check: SQLite rejects
   a reference to a column that does not exist, so it catches the class of error
   a parser cannot. All 9 tables and all 3 views build and return rows.

   Nothing was applied to Postgres. `psql` is installed in this container but
   there is no local server (`initdb`/`pg_ctl` absent), egress to Supabase is
   blocked, and connecting is forbidden regardless.

### [x] Seed script + views produce a correct-looking brief end to end

`python3 src/friday_brief.py --local` renders the full page from seed data with
no database. `briefs/2026-09-25.md` is that output, committed.

Beyond "it rendered", the gate asserts the arithmetic:

- `v_market_funnel` returns exactly three rows, `dk`/`lt`/`global`, zeros included
- Lithuania: 9 touches, 4 replies, 44.4% - matching the seed by hand
- 4 firms per market, proving the joins do not fan out
- MRR totals 3197.00, counting the one converted pilot and not the unconverted one
- ads: 178.55 spend over 2 booked calls = 89.28 per call; NULL for the other three channels
- `v_content_perf` uses only the 2026-09-22 snapshots, never the 09-15 ones
- output is ordered by engagement rate descending

### [x] Every table has RLS enabled and no anon policy

Checked statically against the migration text: all 9 `CREATE TABLE`s have a
matching `ENABLE ROW LEVEL SECURITY`, and `CREATE POLICY` appears in no
migration (comments stripped first, since `002_rls.sql` explains at length why
there is none). `revoke all on schema campaign from anon, authenticated` is
present.

**Not verified, and cannot be from here:** that RLS actually behaves this way at
runtime. That needs a live project and an `anon` request. The verification
queries are written out at the bottom of `002_rls.sql`; running them is item 3 in
Dovy's list below and takes about two minutes.

Worth stating plainly because it looks like an omission: **there are no policies
on purpose.** In Supabase, `service_role` carries `BYPASSRLS`. RLS on with zero
policies is exactly "service role only" - policies only ever grant, so the
absence of them is the control. Adding a restrictive-looking policy would widen
access, not narrow it.

### [x] `campaign_db.py` is idempotent - running the seed twice changes nothing

Proven by full table diff, not by inspection: the gate seeds the mirror, dumps
every row of every table, seeds again, dumps again, and compares. Identical
across all 9 tables and 91 rows.

The seed is hardcoded and contains no randomness and no `now()`, which is what
makes that test meaningful rather than lucky.

Three further checks on the guards:

- a replayed `log_touch` keeps its original `sent_at`
- `record_reply` rejects a sentiment outside the six values
- the `leads` CHECK rejects a 25-49 seat team filed as `too_small`

### [x] No secret in any committed file

Scanned every committed file for JWT-shaped strings and for a filled-in
`SUPABASE_URL=` or `SUPABASE_SERVICE_KEY=` outside `.env.example`. Clean.
`.env.example` ships with both values empty. `.gitignore` excludes `.env`.

The client reads both values from the environment, and `_redact()` strips the key
out of every error message before it can reach a log.

### [x] Branch `campaign/b-ledger`, nothing pushed

Three commits on `campaign/b-ledger`. `git remote -v` is empty.

---

## Departures from the spec, and why

Everything the shared contract fixes was implemented **exactly as written**. The
list below is everything else.

### Columns the spec's list does not name

| Column | Why |
|---|---|
| `companies.dsd_company_id uuid` (nullable, **no FK**) | Phase 0 asks the campaign schema to reference companies DSD already discovered. The DSD table shape was unreadable from this session, so an invented `REFERENCES` would make the migration fail on execution. An empty nullable column leaves the door open for a two-line `004`. See BLOCKED.md B-3. |
| `contacts.natural_key`, `leads.natural_key`, `content.natural_key` | The spec requires idempotency on natural keys. PostgREST's `on_conflict` needs plain column names, so these are computed client-side rather than being expression indexes. Without them there is no upsert target and `upsert_contact` duplicates on every re-run. |

### Constraints the spec does not ask for

- `companies.domain` is **NOT NULL**. The spec says unique index; a nullable
  unique column is not a working de-duplication key, because Postgres allows
  unlimited NULLs in one. If a company has no domain the upsert has nothing to
  match on and creates a second row every run.
- `leads` CHECK: `(team_size = '1-9') = (stage = 'too_small')`. Enforces the
  unambiguous half of the routing table so a small team cannot be handed a
  booking link by an upstream bug. The `qualified` / `gmail_on_request` split is
  deliberately **not** constrained, so a human can upgrade a lead by hand after a
  Gmail firm agrees to move.
- `pilots.seats >= 10`. The offer does not exist below 10 seats. **If Dovy ever
  agrees a 9-seat exception, this constraint blocks it** - one line to drop.
- `touches`: a `reply_sentiment` requires a `replied_at`. `meetings`:
  `pilot_agreed` requires `status = 'held'`.
- `pilots.charge_due_on` is a generated column (`started_on + 14`), so day 14
  cannot drift from what Stripe was told.

### View columns beyond the spec's list

The spec names the columns each view must have; all of them are present. Added
alongside: `positive_replies` and `too_small_leads` (both funnels),
`booked_calls_per_100_contacts` (market), `cost_per_lead_eur`, `impressions` and
`spend_eur` (channel), `click_rate_pct` and `days_since_publish` (content). The
brief uses all of them.

`booked_calls_per_100_contacts` carries a caveat in the SQL: read it only for
markets where outreach is the main channel, since a market whose calls came from
ads has meetings in the numerator that no contact in the denominator produced.

### `campaign_db.py` public surface

Exactly the eleven functions, enforced by a check. **One extra public name:**
`CampaignDBError`, which has to be importable or Batches C/D/E cannot write
`except CampaignDBError`. It is an exception class, not a ledger operation.

The routing helper is private (`_route_stage`) so it does not widen the agreed
surface.

### Files beyond the deliverable list

`tools/validate_sql.py`, `tests/sqlite_mirror.py`, `tests/run_local_proof.py`,
`.gitignore`, and `briefs/2026-09-25.md`. The first three exist because "the
migrations parse" and "the seed is idempotent" are claims that needed evidence
rather than assertion.

---

## Concerns about the shared contract, implemented as specified

None of these were changed. All are implemented exactly as `00-START-HERE.md`
writes them.

**1. A retried submission with a regenerated `submitted_at` will duplicate.**
This is the one worth acting on. The payload carries no idempotency key, so
`insert_lead` derives one from `lower(work_email) + submitted_at`. That is
correct for a genuine second submission on a different day, and correct for an
n8n retry that forwards the *original* `submitted_at`. But if Batch F's retry
path stamps a fresh `submitted_at`, the same form submit lands twice and the lead
count in the brief inflates. **Batch F must forward the browser's original
`submitted_at` unchanged on every retry.** Alternatively a `submission_id` UUID
added to the payload would remove the ambiguity entirely.

**2. `gmail_on_request` is described as a flag but modelled as a stage.** The
routing table calls `qualified` and `too_small` ledger *stages* and
`gmail_on_request` a ledger *flag*. The spec's `leads` column list resolves this
by putting all three in `stage`, which is what shipped. The consequence: a lead
cannot be both `qualified` and flagged for Gmail. In practice they are mutually
exclusive, so this is fine, but if Gmail support ever ships as a normal path,
`stage` will need splitting into stage + email-client-handling.

**3. No consent or capture-context field.** The payload has no consent timestamp,
no source IP and no privacy-policy version. This schema holds work emails and
phone numbers for EU residents, and two of three markets are EU. That is a GDPR
record-keeping question rather than a schema bug, and adding a field nobody
agreed on would have broken the contract, so nothing was added. Flagging it as a
thing to decide before the first real submission.

**4. `pilots.mrr_eur` is EUR but the offer is priced in USD.** The offer is $89
per seat per month; the column the spec names is `mrr_eur`, and ad spend is
genuinely in EUR. Shipped as specified, with a `COMMENT ON COLUMN` saying that
whoever writes it must convert. The Friday brief prints it as EUR. If Stripe
charges in USD, this column will silently be wrong by the exchange rate.

---

## What was skipped, and why

| Skipped | Why |
|---|---|
| Applying the migrations | Global rule 3. Dovy runs them. |
| Connecting to any Supabase project | Global rule 3 and the hard boundary. Also impossible: egress is blocked. |
| Confirming the target project ref | No dashboard access, no `supabase/` dir, sibling repos out of bounds. BLOCKED.md B-1. |
| A foreign key to the DSD tables | Their shape is unreadable from here. B-3. |
| Reading `outreach-engine` to diff the taxonomy | Out of bounds for this session. The six values are taken verbatim from the spec. B-4. |
| Testing RLS behaviour at runtime | Needs a live project and an `anon` request. Verification queries are in `002_rls.sql`. |
| Verifying Postgres enum behaviour and rounding | SQLite has no enums and rounds independently. The client validates all 15 enums in Python first, so a bad value fails with a readable message rather than a `22P02`. |
| `supabase-py` as a dependency | The client is vendored into four repos; a zero-dependency file is easier to keep working than a pinned one. |

---

## What Dovy has to do himself

**About 12 minutes at a keyboard.** Nothing here can happen in the background.

1. **Confirm the target project (1 min).** Open the Supabase dashboard and
   confirm `oqpeebtwtikdzorgouxd` is the lead-pipeline / DSD project. The build
   session could not verify this. Do not run anything until it is confirmed, and
   never against the product project.

2. **Run the three migrations, in order (3 min).** SQL editor, in the confirmed
   project: `001_schema.sql`, then `002_rls.sql`, then `003_views.sql`. All three
   are re-runnable. Check the project ref in the URL bar first.

3. **Verify RLS (2 min).** Run the three queries written out at the bottom of
   `002_rls.sql`. The `pg_policies` one must return **zero rows**. A row there
   means the outreach list is readable from a browser.

4. **Expose the schema to the API (1 min).** Settings -> API -> Exposed schemas
   -> add `campaign` -> save. Without this every call returns `PGRST106` and
   Batches C, D, E and F all fail on their first write. BLOCKED.md B-5.

5. **Copy the credentials into the other repos (3 min).** `SUPABASE_URL` and
   `SUPABASE_SERVICE_KEY` into the `.env` of `outreach-engine`, `reel-engine`,
   `ad-engine` and the n8n credentials. **Note the variable name is
   `SUPABASE_SERVICE_KEY`, not `SUPABASE_SERVICE_ROLE_KEY`** - deliberate, see
   below.

6. **Check the taxonomy against the live classifier (1 min).** Grep
   `outreach-engine` for the values its classifier emits and confirm they are
   exactly `hot_pain`, `curious`, `endorse`, `objection`, `unrelated`,
   `ineligible`. Different casing or spelling will be rejected by the enum.
   BLOCKED.md B-4.

7. **Optional, deferrable (5 min).** Confirm the DSD company table name and PK
   type, and a short `004_dsd_link.sql` can add the foreign key on
   `companies.dsd_company_id` plus a backfill on `domain`. Not needed for the
   campaign to run. B-3.

### One thing to read before step 5

**This build container's ambient `SUPABASE_URL` pointed at the product database
`kngcxwcybozgqgnoweyt`, with a matching `SUPABASE_SERVICE_ROLE_KEY` set beside
it.** Nothing here connected to it. But any script in any of these repos that ran
with that environment as-is would have aimed at the product database.

Two mitigations shipped: `campaign_db.py` reads `SUPABASE_SERVICE_KEY` (a name
nothing else in the environment sets), and it raises before opening a connection
if `SUPABASE_URL` points at that project. The product ref appears in none of the
`.sql` files, so nothing pasted into a SQL editor can aim at it either.

When setting up the other repos, make sure each one's `SUPABASE_URL` is the
ledger project, and leave the guard in place. BLOCKED.md B-2.

---

## Verifying this without a database

```bash
pip install sqlglot
python3 tools/validate_sql.py       # parse-check the migrations
python3 tests/run_local_proof.py    # 42 checks, the whole QA gate
python3 src/friday_brief.py --local --no-write   # see the brief render
```
