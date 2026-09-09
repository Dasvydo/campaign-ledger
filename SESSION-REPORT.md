# Session report - Batch B (`campaign-ledger`)

2026-09-09, branch `claude/campaign-build-status-9j9194`, from a clean clone.
No credentials were used, no migration was run, nothing connected to Supabase.

## Verified

Every number below was observed in this session, not carried over from
`RUN-REPORT.md`.

| Command | Result |
|---|---|
| `pip install -r requirements.txt` | installed sqlglot 30.18.0, the only dependency |
| `python3 tests/run_local_proof.py` | **46 passed, 0 failed** on the clean clone, exit 0 |
| `python3 tools/validate_sql.py` | **3 files ok, 67 statements fully parsed**, exit 0 |
| `python3 src/friday_brief.py --local --no-write` | rendered in full, all 6 sections |
| `python3 tests/run_local_proof.py` (after this session's changes) | **51 passed, 0 failed** |
| `python3 tests/schema_drift.py` | **0 drift** in all four classes, exit 0 |

Both gate numbers matched the expected values exactly, so there is nothing to
investigate there.

One thing worth stating precisely, because the two readings differ. The
validator's per-file lines are:

```
ok    001_schema.sql: 67 statements (52 fully parsed, 15 opaque)
ok    002_rls.sql: 23 statements (3 fully parsed, 20 opaque)
ok    003_views.sql: 12 statements (12 fully parsed, 0 opaque)
```

That is **102 statements in total, of which 67 parsed into a full AST** and 35
came back opaque. The expected "67 statements parsed" is the fully-parsed
count, and it matches. It is not the count of statements in the migrations, and
it coincidentally equals the statement count of `001_schema.sql` alone.

The rendered brief announces itself on the first line:

```
> **SEED DATA, NOT REAL.** Rendered from the local SQLite mirror and
> `seed/seed_demo.py`. Every figure below is invented.
```

All six sections render. No figure is blank, and no cell contains `None`,
`nan` or `null`: the two matches for "none" in the text are the English word in
"and none held". Nine cells render as `-`, which is the renderer's deliberate
"no number here" rather than a missing value, with one qualification under
**Found**.

The market and channel tables reconcile against each other and against the
headline: leads 2+3+5 = 3+2+4+1 = 10, booked 1+2+3 = 3+0+2+1 = 6, held
0+2+0 = 2+0+0+0 = 2, touches 8+9+8 = 25 = the outreach row.

## Produced

| Path | What it is |
|---|---|
| `tests/schema_drift.py` | new. The column drift audit, runnable standalone for a full column map |
| `tests/run_local_proof.py` | five new checks wired into the gate, 46 to 51 |
| `RUN-REPORT.md` | new "No column drift" section; present-tense counts moved to 51 |
| `requirements.txt` | check count moved to 51 |

Three commits, each revertible on its own: `80ace66`, `2520c9e`, `80c63b1`.

### The drift audit

`tests/schema_drift.py` parses `migrations/001_schema.sql` with sqlglot and
`src/campaign_db.py` with `ast`. Neither file is imported or executed, so the
audit is a property of the committed text, not of a particular run, and it
cannot be satisfied by a lucky code path.

It checks four drift classes:

| Class | What fails it |
|---|---|
| unknown column | the client writes a column the table does not have |
| type drift | the written value's type family cannot land in that Postgres type |
| enum drift | a `_check` allowlist disagrees with its `create type ... as enum` |
| writer drift | a table gains or loses a writer |

The rule that does the real work is TEXT_ID against TEXT_MADE. A bare `str`
parameter whose name ends in `_id` may land in a `uuid` column; a string the
function *built* (a slug, an f-string, a normalised domain) may not. That is
the 2026-09-08 Batch E bug written as a rule rather than as a warning.

**Current result: zero drift.** All 71 written columns exist, all resolvable
types are compatible, and all 14 enum allowlists match their `CREATE TYPE`
exactly. The client and the schema are in agreement today.

**It resolves 59 of 71 written columns to a concrete type family.** The 12 it
cannot are `insert_lead`'s `payload.get()` reads: the payload is
`Mapping[str, Any]`, so its element types are genuinely unknown until runtime.
An UNKNOWN family never fails a type check, so a fifth check asserts the
resolution share itself. Without it the audit could rot into UNKNOWN everywhere
and keep passing forever.

**Proved by injecting drift and watching it fail**, one class at a time, each
reverted immediately after:

| Injected | Check that failed | Message |
|---|---|---|
| `_slug(campaign_name)` into `ad_stats.creative_content_id` | every written value can land in the column it targets | `ad_stats.creative_content_id is UUID but snapshot_ad_stats() writes a TEXT_MADE value (campaign_db.py:681)` |
| an `engagement_rate` key on the `content_stats` row | every column campaign_db writes exists in 001_schema.sql | names the column and the writing function |
| a seventh value `maybe` in `_SENTIMENTS` | the client's enum allowlists match the schema's enums | lists the client-only value |
| `_iso()` instead of `_day()` for `content_stats.captured_on` | every written value can land in the column it targets | names the DATE column and the TS value |

The first injection is the 2026-09-08 bug reproduced exactly, and it is caught
statically, before the seed reaches it. `git status` was clean after each
revert.

## Found

Nothing here was changed. Items 1 to 4 are editorial judgements about the
brief, which the task asked me to report rather than act on, and item 5 is an
operational gap rather than a defect.

### 1. One suggestion in the brief is a fixed sentence that does not track its own number

In `suggestions()`, the paid-channel line interpolates the cost and then
appends a constant clause:

```
- **Meta ads costs EUR 89.28 per booked call** at EUR 178.55 spent. Against
  $89 per seat per month, one 10 seat firm covers that in the first week of a
  paid month.
```

The second sentence is a literal string. It is emitted whatever the first
number says. Rendering the same function with the cost raised to EUR 4,500 a
booked call produces:

```
- **Meta ads costs EUR 4,500.00 per booked call** at EUR 9,000.00 spent.
  Against $89 per seat per month, one 10 seat firm covers that in the first
  week of a paid month.
```

That is false by roughly a factor of twenty. A 10 seat firm at $89 a seat is
about EUR 780 a month, so a week of it is about EUR 182: the claim holds only
while cost per booked call stays under roughly EUR 180. This week it is EUR
89.28, so the sentence is true today by luck of the numbers, not because
anything checked. This is the one item I would treat as a bug rather than a
matter of taste, because the brief is designed to be read at a glance and the
claim is the part a reader would act on.

### 2. The thin-evidence marker does not cover the ad targeting claim

`friday_brief.py`'s module docstring states the design principle plainly: "any
line resting on a denominator under `THIN_EVIDENCE` is marked as thin", with
`THIN_EVIDENCE = 30`. The market comparison honours it, and says so:

```
Both sides of that comparison are under 30 touches, so it is a hint, not a
result. Do not cut a market on it yet.
```

Two lines resting on **4 leads** carry no such marker:

```
- **Meta ads is pulling the wrong size of firm.** 2 of 4 leads are 1-9 seats.
```
```
- 50% of ad leads are too small for the offer. That is a targeting cost, not a
  conversion problem.
```

The trigger is `share >= 0.3` with no floor on the denominator, so one extra
small lead out of three would fire it. "Pulling the wrong size of firm" and
"that is a targeting cost, not a conversion problem" are both conclusions, and
n=4 does not support either. The brief marks a comparison on 8 and 9 touches as
thin while stating a conclusion on 4 leads flatly, which is the opposite of the
stated principle. A denominator floor on this branch would fix it.

### 3. The content lane ranking compares three placements against one

```
- **HyperFrames is the strongest lane on engagement** at 5.11% across 3
  placement(s), against Static ad at 0.52%.
```

The measured seed data behind that:

| Lane | Placements | Total views | Mean engagement | Spread |
|---|---|---|---|---|
| HyperFrames | 3 | 8,060 | 5.11% | 2.59% to 7.41% |
| Dovy on camera | 2 | 2,870 | 3.88% | 1.64% to 6.11% |
| Higgsfield | 3 | 10,350 | 1.35% | 0.49% to 2.68% |
| Static ad | **1** | 8,800 | 0.52% | single reading |

Two problems. The winning lane's mean spans 2.59% to 7.41%, a near threefold
range, so the mean is not a stable summary of three points. And the losing side
of the comparison is a single placement, presented as a lane average.

`THIN_EVIDENCE` is never applied to content at all. The only guard is a
separate ad-hoc threshold, `views_by_lane[best_lane] < 5000`, which checks the
**best** lane's views only. HyperFrames has 8,060, so no caveat printed. Nothing
looks at the number of placements on either side, or at the losing lane at all.

### 4. The brief mixes EUR and USD in the same comparison

The header prints "$89 per seat per month + $500 setup"; every money figure
below it is EUR. The item 1 sentence compares a EUR cost directly against a USD
price with no conversion. `pilots.mrr_eur` carries a schema comment
acknowledging this ("the offer is priced in USD ... but this column is EUR, per
spec. Whoever writes it must convert"), and the seed does convert (41 seats at
$89 is $3,649, stored as EUR 3,197.00, about 0.876). So the intent is settled
and the seed is self-consistent. What is missing is enforcement: there is no
writer function for `pilots` (see item 5), so nothing in code performs or
checks that conversion, and a live writer storing USD would be silently wrong
in the headline MRR figure. The words "per spec" in that comment suggest this
may be a founder decision already taken, so I have not touched it.

### 5. `meetings` and `pilots` have no writer anywhere in the live pipeline

The brief's headline line is "10 leads, 6 calls booked, 2 held, 1 pilot
converted, EUR 3,197.00 MRR". The last three of those five figures come from
`campaign.meetings` and `campaign.pilots`, and **`campaign_db` exports no
function that writes either table.** The seed reaches the private client
directly, with a comment saying this is deliberate: "the eleven exported by
`campaign_db` are the ones the other batches need, and neither engine writes a
meeting."

That is a defensible decision about the client's surface. The gap it leaves is
operational: if no engine writes a meeting and no client function exists, then
in the live ledger "booked", "held", "pilots", "converted" and "MRR" stay at
zero unless somebody enters rows by hand in Supabase, and no documented process
says who does that or when. The Friday brief would then report a funnel that
stops at leads, every week, with no error anywhere to explain why.

This needs a decision, not a code change, so I have not made one. The audit
records the two tables in `WRITERLESS_TABLES` as an explicit expectation, so
adding a writer later, or losing one on any other table, fails the gate rather
than passing silently.

### 6. Low severity: a real zero renders as "no data" in one table and as 0.00 in another

`_eur()` is documented as "a number, or a dash. Never a fabricated zero", but
the channels table applies it as `_eur(c["spend_eur"]) if c["spend_eur"]
else "-"`. A genuine spend of 0.00 is falsy, so it renders as `-`, the same
glyph used for "not applicable". The appendix prints the same fact as `EUR
0.00`:

```
| Outreach | 25 | 3 | 3 | 0 | 3 | 2 | - | - | - |          <- table
  Outreach: 3 / 3 / 0 / 3 / 2 / EUR 0.00                    <- appendix
```

The same treatment of `touches_sent` is explained in the surrounding prose and
reads as intentional; the spend column is not explained. Cosmetic, but the two
renderings of one fact are inconsistent.

Also cosmetic: the brief prints "1 booked call(s)", so the pluralisation
fallback shows in output the founder reads.

### 7. `RUN-REPORT.md` still says 46 in one place, on purpose

Line 11, under the dated heading "Decisions applied - 2026-09-06", records "46
checks, 0 failures" for that run. I left it as written because it is a true
statement about that run. The present-tense counts elsewhere in the file, and
in `requirements.txt`, now say 51.

## Blocked

**I could not read the parked decisions P-1 to P-6.** They live in
`campaign-n8n/ops/`, and this session's repository scope is
`dasvydo/campaign-ledger` only. I therefore cannot confirm that none of the
seven findings above collides with a parked item. Items 4 and 5 are the two I
would check first: both are decisions about money and process rather than code,
and `pilots.mrr_eur`'s schema comment says the EUR choice was made "per spec".
I have acted on none of them. To clear this: grant read access to
`campaign-n8n`, or paste the P-1 to P-6 list.

**The drift audit checks the committed schema, not the deployed one.** It
compares `src/campaign_db.py` against `migrations/001_schema.sql` as committed.
If `001_schema.sql` was applied to Supabase and the live database was then
edited in the SQL editor, or if the migration was never applied, the audit
cannot see it and would still report clean. Closing that needs one read-only
introspection query against `information_schema.columns` for schema `campaign`
in the target project, diffed against `schema_columns()`. That needs a
credential and a live connection, both of which are out of bounds here, and it
is the same wall as the existing `BLOCKED.md` B-6.

**Twelve columns cannot be type-checked statically.** `insert_lead` reads its
values from a `Mapping[str, Any]` payload via `payload.get()`, so
`company_name`, `phone`, `role`, `market`, `locale`, `source`, `team_size`,
`email_client` and the four `utm_*` columns resolve to UNKNOWN and can never
fail the type check. Six of the twelve are covered at runtime by `_check`
against an enum allowlist, which the audit does verify against the schema. The
remaining six are text columns, where the risk is low. Closing this properly
needs the payload to become a typed object (a `TypedDict` or dataclass) at the
Batch A and Batch F boundary, which is a cross-repo contract change and not
mine to make.

**I did not verify how the other five batches actually call `campaign_db`.**
The premise of this audit is that they import it and fall back to a JSONL shim.
Only `campaign-ledger` is in scope this session, so the shim's own column names
were never compared against the schema. A shim that writes a column the real
client does not would fail on the first live write in exactly the way this
audit is meant to prevent, and it is invisible from here.

**Not done, and deliberately:** no migration was run, no Supabase connection
was made, nothing was imported into n8n, no paid API was called, no repository
visibility was changed, and no Danish or Lithuanian copy was touched.
