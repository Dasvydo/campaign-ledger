# campaign-ledger

The one place that knows the whole funnel for the DoviLoop Teams campaign
(8 Sept - 19 Oct 2026).

> **Campaign-wide documents live in `campaign-n8n/ops/`.** This repo is one of six
> batches; the status of all of them, the setup guide for a new machine, the
> decisions taken and what is still waiting on a human are kept together there:
>
> | File | What |
> |---|---|
> | `ops/STATUS.md` | audit of all six batches |
> | `ops/NEW-PC-SETUP.md` | clone, install and prove every repo from scratch |
> | `ops/DECISIONS.md` | what was decided, why, and how to reverse it |
> | `ops/NIGHT-RUN.md` | the current task plan and its live status |
> | `ops/HANDOFF.md` | what to pick up next |
>
> The six repos must be cloned as **siblings under one parent directory** -
> several tools reach across them by relative path, and this repo's own contract
> tests locate `campaign-ledger` that way.


Instantly knows who was emailed. Google Calendar knows who booked. Stripe knows
who paid. Buffer knows what posted. None of them can answer *"calls booked per
100 contacts, by market"*, which is the number the three-market A/B test gets
decided on. This repo is where that number comes from.

```
migrations/001_schema.sql   schema, 15 enums, 9 tables, indexes
migrations/002_rls.sql      RLS on everything, service role only, no policies
migrations/003_views.sql    v_market_funnel, v_channel_funnel, v_content_perf
src/campaign_db.py          the client Batches C, D, E and F write through
src/friday_brief.py         reads the views, renders one page of markdown
seed/seed_demo.py           fake rows so the views can be tested before real data
tools/validate_sql.py       parse-checks the migrations without connecting
tests/sqlite_mirror.py      runs the committed SQL on in-memory SQLite
tests/run_local_proof.py    the QA gate, as code
briefs/                     rendered briefs, one per Friday
```

---

## Where this lives, and where it must never live

The ledger goes in the **lead-pipeline Supabase project**, in a **new schema
called `campaign`**. That project already holds the DSD LinkedIn discovery data,
which keeps the outreach story in one place.

That project is **`oqpeebtwtikdzorgouxd`, confirmed by Dovy on 2026-09-06**
(`BLOCKED.md` B-1, resolved). Still check the project ref in the URL bar before
running anything.

The offer the ledger tracks is priced at **89 USD per seat per month plus a 500
USD one-off setup**. Any ROI figure quoted anywhere in the campaign (ad copy,
reel hooks, the landing page) is **modelled, not measured**; this ledger records
funnel counts and spend, never an ROI.

The **product database (Frankfurt)** is off limits: no migration, no client, no
read, no connection string. `src/campaign_db.py` refuses to build a client if
`SUPABASE_URL` points at it, and its ref appears in none of the `.sql` files, so
nothing you paste into a SQL editor can aim at it.

---

## Setup, in order

### 1. Run the migrations. Nobody else does this.

Open the SQL editor in the lead-pipeline project and run, **in this order**:

1. `migrations/001_schema.sql`
2. `migrations/002_rls.sql`
3. `migrations/003_views.sql`

All three are re-runnable. Check the project ref in the URL bar first.

Then verify by hand (the queries are written out at the bottom of `002_rls.sql`):

```sql
-- every table true
select tablename, rowsecurity from pg_tables where schemaname = 'campaign';

-- MUST be empty. a row here means the outreach list is readable from a browser
select policyname from pg_policies where schemaname = 'campaign';
```

### 2. Expose the schema to the API

Supabase only serves schemas listed under **Settings -> API -> Exposed schemas**.
Add `campaign` and save. Without it every call returns `PGRST106`.

### 3. Fill in `.env`

```bash
cp .env.example .env
```

`SUPABASE_URL` is the project REST URL. `SUPABASE_SERVICE_KEY` is the service
role key.

The variable is `SUPABASE_SERVICE_KEY`, **not** `SUPABASE_SERVICE_ROLE_KEY`. That
is deliberate: several environments already export `SUPABASE_SERVICE_ROLE_KEY`
pointing at the product project, and reading a different name means this code can
never pick that one up by accident.

The service key bypasses RLS and can read every contact, work email and phone
number in the schema. Server side only. Never in a browser bundle, never in an
n8n node that renders into a page, never in a log line.

### 4. Copy the same two values into the other repos

Batches C, D and E import `campaign_db` and need the same `SUPABASE_URL` and
`SUPABASE_SERVICE_KEY`.

---

## The client

`src/campaign_db.py` exposes eleven functions and nothing else.

```python
upsert_company(name, domain, market, *, segment=None, country=None,
               est_size=None, uses_m365=None, has_dev_team=None,
               fit_score=None, hook_seed=None, source="icp_finder",
               dsd_company_id=None) -> dict

upsert_contact(company_id, market, *, full_name=None, role_guess=None,
               linkedin_url=None, email=None, email_source=None) -> dict

log_touch(contact_id, channel, language, *, sequence_step=1, sent_at=None,
          notes=None) -> dict

record_reply(contact_id, channel, sentiment, *, sequence_step=1,
             replied_at=None, notes=None, touch_id=None) -> dict

insert_lead(payload, *, company_id=None, stage=None) -> dict

upsert_content(kind, lane, language, hook, *, script_path=None,
               asset_path=None, platforms=None, published_at=None,
               buffer_id=None) -> dict

snapshot_content_stats(content_id, platform, captured_on, *, views=0,
                       likes=0, comments=0, saves=0, clicks=0) -> dict

snapshot_ad_stats(campaign_name, ad_set_name, captured_on, *,
                  creative_content_id=None, spend_eur=0, impressions=0,
                  clicks=0, leads=0) -> dict

get_market_funnel() -> list[dict]
get_channel_funnel() -> list[dict]
get_content_perf(*, limit=None) -> list[dict]
```

`CampaignDBError` is also importable, so callers can write
`except CampaignDBError`. It is the only public name beyond the eleven.

### Every write is idempotent

The outreach engine re-runs. Running it twice must not create a second row or
move a timestamp. Each function targets a unique constraint:

| Function | Natural key |
|---|---|
| `upsert_company` | `domain`, lowercased and stripped of scheme, `www.` and path |
| `upsert_contact` | `(company_id, linkedin_url or email or name)` |
| `log_touch` | `(contact_id, channel, sequence_step)` |
| `insert_lead` | `lower(work_email) + submitted_at` |
| `upsert_content` | `kind:lane:language:slug(hook)` |
| `snapshot_content_stats` | `(content_id, platform, captured_on)` |
| `snapshot_ad_stats` | `(campaign_name, ad_set_name, captured_on)` |

Two different behaviours, on purpose:

- **Facts about the past are insert-if-absent.** A touch that already went out on
  Tuesday keeps Tuesday's `sent_at` when Thursday's run replays it. Re-dating it
  would corrupt every reply-rate number in the brief.
- **Facts that get enriched are merged.** What we know about a company, or
  today's view count, is overwritten by the newer reading.

### insert_lead takes the shared contract verbatim

`payload` is the qualifier object from `00-START-HERE.md`, exactly as Batch A's
form emits it:

```python
insert_lead({
    "source": "ad", "market": "global", "locale": "en",
    "utm": {"source": "meta", "medium": "retargeting",
            "campaign": "teams-retarget", "content": "static-roi"},
    "company_name": "Harbor Point Accounting",
    "work_email": "dana@harborpointcpa.com",
    "phone": "+1 415 555 0101",
    "team_size": "50+", "email_client": "outlook", "role": "owner_partner",
    "submitted_at": "2026-09-15T18:20:00+00:00",
})
```

`stage` is derived from `team_size` and `email_client` unless you pass it, so the
ledger cannot disagree with the landing page about who was shown a booking link:

| Condition | Stage |
|---|---|
| 10+ seats, Outlook | `qualified` |
| 10+ seats, Gmail or other | `gmail_on_request` |
| 1-9 seats | `too_small` |

A CHECK constraint enforces the `1-9 <-> too_small` half of that in the database,
so a small team can never be handed a booking link by a bug upstream. The
`qualified` / `gmail_on_request` split is left unconstrained so a human can
upgrade a lead by hand once a Gmail firm agrees to move.

### The six-value reply taxonomy

`record_reply` accepts exactly these, the canonical campaign-wide set since
2026-09-06 and identical to what the outreach engine's classifier emits:

```
interested   not_now   not_a_fit   referred   objection   unsubscribe
```

They are a Postgres enum. Anything else is rejected in Python first, with a
message that names the field. The previous set was retired on 2026-09-06; see
`RUN-REPORT.md`, "Decisions applied".

How the views read them, and how the brief reports them:

| Value | Counts as a reply | Counts as positive | Reading |
|---|---|---|---|
| `interested` | yes | **yes** | wants to talk |
| `referred` | yes | **yes** | pointed us at the right person |
| `not_now` | yes | no | neutral: timing, come back later |
| `objection` | yes | no | neutral: pushback that can be answered |
| `not_a_fit` | yes | no | negative: wrong size, has a dev team, wrong segment |
| `unsubscribe` | yes | no | negative: asked to be left alone |

So in `v_market_funnel` and `v_channel_funnel`: `replies` is every touch with a
non-null `reply_sentiment`, `positive_replies` is `interested` or `referred`,
and `reply_rate_pct` is `replies / touches_sent`. The same definition is written
as a SQL comment at the top of `003_views.sql` and on both views.

### One thing this client will not police

`log_touch(contact_id, "email", ...)` for a Danish contact will be recorded
without complaint. Denmark's marketing law is stricter than the rest of the EU on
unsolicited commercial email, and until Dovy confirms the position **no Danish
address goes into Instantly**. That rule belongs in the outreach engine, which
decides what to send. This table only records what happened. (`seed_demo.py`
asserts it for its own data.)

---

## The Friday brief

```bash
python3 src/friday_brief.py                    # live ledger
python3 src/friday_brief.py --local            # SQLite mirror + seed data
python3 src/friday_brief.py --date 2026-10-02  # a specific week ending
python3 src/friday_brief.py --no-write         # stdout only
```

Writes `briefs/YYYY-MM-DD.md` and prints the same text to stdout, so n8n can read
the file or pipe the output into an email node.

One page: the three-market funnel, the channel table, top and bottom three
content pieces, ad spend against leads, and a "what the numbers suggest" section.
That last section names the weakest market and the weakest channel plainly, marks
any comparison resting on fewer than 30 touches as thin, and the page then **ends
with the raw numbers** rather than a recommendation. In week two of a six week
campaign the honest answer is usually "not enough data yet", and a brief that
hides that behind a confident sentence is worse than no brief.

`briefs/2026-09-25.md` is a committed example, rendered from seed data. It says
so in its first line.

---

## Testing, without a database

Nothing in this repo connects to anything. Three commands:

```bash
pip install sqlglot                 # the only dependency, and only for tests
python3 tools/validate_sql.py       # parse-check the migrations
python3 seed/seed_demo.py --local   # seed an in-memory SQLite mirror
python3 tests/run_local_proof.py    # the whole QA gate
```

`tests/sqlite_mirror.py` reads the **committed** `.sql` files, mechanically
rewrites the handful of constructs SQLite does not share with Postgres (enums to
text, `DO` blocks dropped, `now()` and `gen_random_uuid()` swapped), and runs
them in memory. The real client, the real seed and the real brief then run on top
unmodified. So the view logic under test is the view logic that ships.

It does not cover enums, RLS, or Postgres rounding. Those are listed as
unverified in `RUN-REPORT.md`.

`src/campaign_db.py` itself has **no dependencies at all** - plain `urllib` and
PostgREST - because it gets vendored into four other repos.

---

## The seed data

`seed/seed_demo.py` writes about 90 rows: 12 firms across three markets, 13
contacts, 25 touches, 10 replies covering all six sentiments (3 `interested`,
2 `not_now`, 2 `objection`, 1 `not_a_fit`, 1 `referred`, 1 `unsubscribe`), 10
leads covering every routing outcome, 6 meetings, 2 pilots, 7 content items and
two days of stats for each. The one ROI figure in it, the static ad's "9x
return" hook, is a fixture mirroring ad copy and is a modelled number.

Every value is hardcoded. No randomness, no `now()`. That is what makes the
idempotency test meaningful: run it twice and every row is byte-identical.

**Do not run it against the live ledger once real data exists.**

---

See `AUDIT.md` for what could and could not be confirmed before the build,
`BLOCKED.md` for the open items (four open, two resolved), and `RUN-REPORT.md`
for what shipped, what did not, the decisions applied on 2026-09-06, and what
Dovy has to do himself.
