# AUDIT - Batch B (`campaign-ledger`)

Phase 0, run 2026-09-03, unattended session, before any other file in this repo
was created or changed.

---

## 1. Which Supabase projects exist, and which is the DSD lead project

**Not confirmable from inside this session. Nothing below is a guess.**

What I actually checked, and what came back:

| Check | Result |
|---|---|
| `find / -maxdepth 4 -type d -name supabase` | no `supabase/` directory anywhere on the container |
| `.env` / `.env.*` files inside `/home/user/campaign-ledger` | none - repo held only a placeholder `README.md` and `.git` |
| `supabase-architect` skill notes | no such skill installed. `/root/.claude/skills/` holds only `session-start-hook` and one synced skill bundle, neither Supabase-related |
| The `outreach-engine` repo (where the DSD tables are described) | **out of bounds for this session.** The batch brief forbids reading sibling repos, so I did not open it. This is the single biggest reason the project ref cannot be confirmed here |
| Session environment variables | `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` are both set - see the finding below |
| Reachability of `yheilbuunzdugfnermfb.supabase.co` | one unauthenticated request to the REST root returned HTTP `000` (no connection - egress blocked / host not resolvable from this container). No project was connected to, no credential was sent, no data was read |

### Finding 1 - the ambient credentials in this session point at the FORBIDDEN project

`SUPABASE_URL` in this container resolves to project ref **`kngcxwcybozgqgnoweyt`**.

That is the **product database (Frankfurt)** - the hard boundary. A matching
`SUPABASE_SERVICE_ROLE_KEY` is also present in the environment.

Consequences, all of which are handled in the build:

- I did not connect to it, read it, write it, or name it as a target anywhere in
  the shipped code. It appears in this repo only in this audit line and in the
  guard that refuses it.
- Any script in this repo that ran with the ambient environment as-is would have
  aimed at the product database. That is a live footgun, not a theoretical one.
- `src/campaign_db.py` therefore reads **`SUPABASE_SERVICE_KEY`** (the name the
  spec fixes) and **not** `SUPABASE_SERVICE_ROLE_KEY`, so the ambient key cannot
  be picked up by accident, and it hard-refuses at startup if `SUPABASE_URL`
  points at `kngcxwcybozgqgnoweyt`.
- Batches C, D and E import this client. The guard protects them too.

### Finding 2 - the lead-pipeline project ref is unconfirmed (resolved 2026-09-06)

The spec names `yheilbuunzdugfnermfb` as the existing lead-pipeline project that
holds the DSD LinkedIn discovery data. Nothing inside the build session could
confirm that project exists, that Dovy owns it, or that it is the DSD project.
**Dovy confirmed it on 2026-09-06**: the ledger lives in `yheilbuunzdugfnermfb`,
schema `campaign`. The paragraphs below describe the position at build time.

Per the batch brief, the migration is written **against that named project, in a
new schema `campaign`**, and the uncertainty is carried forward loudly:

- flagged here,
- flagged in `BLOCKED.md`,
- flagged in `RUN-REPORT.md`,
- and stated at the top of `migrations/001_schema.sql` itself, so whoever pastes
  it into a SQL editor reads the warning before running it.

**I did not fall back to the product project, and did not invent a substitute.**

---

## 2. The existing DSD table structure

**Not reachable.** Two independent reasons:

1. The DSD tables live in a Supabase project this session cannot connect to (and
   is forbidden from connecting to).
2. Their DDL, if it is checked in anywhere on this container, is in
   `outreach-engine`, which this session must not read.

So the campaign schema **cannot** reference DSD companies by foreign key without
inventing a column type, a table name and a key shape I have not seen. Inventing
that would produce a migration that fails on `REFERENCES` at run time.

What I did instead, so the linkage is still possible later without a second
migration: `campaign.companies` carries a nullable, unconstrained
`dsd_company_id uuid` column. It has no foreign key and no default. If Dovy
confirms the DSD table and key type, a two-line follow-up migration can add the
constraint. If the DSD key turns out not to be a `uuid`, the column is dropped
and re-added - cheap, because it is empty.

This is the one place the schema carries a column the spec's column list does not
name. It is called out again in `RUN-REPORT.md`.

De-duplication against DSD therefore stays **manual/deferred**, and is logged in
`BLOCKED.md`.

---

## 3. The six-value reply taxonomy

The spec states the taxonomy in use and instructs reuse rather than reinvention.
I could not read it from the outreach engine's source (out of bounds), so at
build time I took the six values exactly as the spec wrote them.

**Superseded on 2026-09-06.** The spec's set and the outreach engine's set turned
out to differ, and Dovy resolved the conflict in favour of the outreach engine's
set. The canonical six, now in the enum, the client and the seed, are:

```
interested, not_now, not_a_fit, referred, objection, unsubscribe
```

The retired set is recorded once, in `RUN-REPORT.md` under "Decisions applied",
and nowhere else in this repo.

These are the Postgres enum `campaign.reply_sentiment`. No seventh value, no
`null`-as-a-category, no parallel vocabulary, no renaming. `touches.reply_sentiment`
is nullable - a touch with no reply yet has `NULL`, which is the absence of a
classification rather than a class of its own.

The risk logged at build time (that the engine's casing or spelling would differ
from the enum) is closed by the same decision: the enum now *is* the engine's
set. `BLOCKED.md` B-4 is resolved.

---

## 4. What this audit means for the build

| Decision | Because |
|---|---|
| Migration targets `yheilbuunzdugfnermfb`, schema `campaign` | spec's named target; unconfirmed at build time, confirmed by Dovy 2026-09-06 |
| No DSD foreign key, only a nullable `dsd_company_id` | DSD table shape unreadable from here |
| Reply sentiment enum is exactly six values | spec instruction at build time; since 2026-09-06 the six are the outreach engine's set (see section 3) |
| Client reads `SUPABASE_SERVICE_KEY`, refuses `kngcxwcybozgqgnoweyt` | ambient session credentials point at the product DB |
| SQL is validated by local parse only, never applied | global rule 3 - Dovy runs migrations |
| `companies.domain` is `NOT NULL` and lowercased | it is the natural key upserts de-duplicate on; a nullable unique column would let duplicates through (Postgres allows many `NULL`s in a unique index) |

Nothing in this repo connects to any database at build time or at test time.
