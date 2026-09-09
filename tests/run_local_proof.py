#!/usr/bin/env python3
"""The QA gate, run as code instead of asserted in prose.

    python3 tests/run_local_proof.py

Nothing here connects to a database. It builds the SQLite mirror from the
committed migrations, runs the real seed and the real Friday brief on top of it,
and checks every line of the batch spec's QA gate that can be checked offline.

Exit code 0 means every check below passed.
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests"), str(ROOT / "seed")]

import campaign_db  # noqa: E402
import friday_brief  # noqa: E402
import seed_demo  # noqa: E402
import schema_drift  # noqa: E402
import sqlite_mirror  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    (PASSED if condition else FAILED).append(
        f"{name}{(' - ' + detail) if detail and not condition else ''}")
    print(f"{'PASS' if condition else 'FAIL'}  {name}"
          + (f"\n        {detail}" if detail and not condition else ""))


# ---------------------------------------------------------------------------
# 1. Migrations parse (delegated to tools/validate_sql.py)
# ---------------------------------------------------------------------------

def check_migrations_parse() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "validate_sql.py")],
        capture_output=True, text=True)
    check("migrations parse under sqlglot's postgres dialect",
          result.returncode == 0, result.stdout + result.stderr)


# ---------------------------------------------------------------------------
# 2. RLS on every table, no policy anywhere
# ---------------------------------------------------------------------------

def check_rls() -> None:
    schema = (ROOT / "migrations" / "001_schema.sql").read_text()
    rls = (ROOT / "migrations" / "002_rls.sql").read_text()

    tables = set(re.findall(r"create table if not exists campaign\.(\w+)",
                            schema))
    enabled = set(re.findall(
        r"alter table campaign\.(\w+)\s+enable row level security", rls))
    missing = sorted(tables - enabled)
    check(f"RLS enabled on all {len(tables)} tables", not missing,
          f"missing: {missing}")

    # Comments are stripped first: 002_rls.sql explains at length why there is
    # no CREATE POLICY in it, and that explanation must not trip its own check.
    everything = "\n".join(
        re.sub(r"--.*$", "", p.read_text(), flags=re.M).lower()
        for p in (ROOT / "migrations").glob("*.sql"))
    check("no CREATE POLICY anywhere (no policy = service role only)",
          "create policy" not in everything)
    check("anon and authenticated are revoked from the schema",
          "revoke all on schema campaign from anon, authenticated" in rls)


# ---------------------------------------------------------------------------
# 3. The client's public surface is exactly the eleven agreed names
# ---------------------------------------------------------------------------

def check_client_surface() -> None:
    expected = {
        "upsert_company", "upsert_contact", "log_touch", "record_reply",
        "insert_lead", "upsert_content", "snapshot_content_stats",
        "snapshot_ad_stats", "get_market_funnel", "get_channel_funnel",
        "get_content_perf",
    }
    check("campaign_db.__all__ is exactly the eleven agreed functions",
          set(campaign_db.__all__) == expected,
          f"extra: {set(campaign_db.__all__) - expected}, "
          f"missing: {expected - set(campaign_db.__all__)}")

    # CampaignDBError is the one public name beyond the eleven. It has to be
    # importable or the other batches cannot write `except CampaignDBError`.
    # It is an exception class, not a ledger operation, so it does not widen
    # the agreed function surface.
    public = {n for n in dir(campaign_db)
              if not n.startswith("_")
              and callable(getattr(campaign_db, n))
              and getattr(getattr(campaign_db, n), "__module__", "")
              == "campaign_db"
              and not (isinstance(getattr(campaign_db, n), type)
                       and issubclass(getattr(campaign_db, n), Exception))}
    check("no extra public function leaks out of campaign_db",
          public == expected, f"unexpected: {sorted(public - expected)}")


# ---------------------------------------------------------------------------
# 4. Column drift between the client and the schema
#
# The other batches import campaign_db and fall back to a local JSONL shim when
# it is absent, so a column the client writes but the schema does not have
# passes every offline test in every repo and fails on the first live write.
# tests/schema_drift.py reads both sides out of the committed text - sqlglot for
# the migration, ast for the client - and never imports or connects to either.
# ---------------------------------------------------------------------------

def check_schema_drift() -> None:
    findings = schema_drift.audit()

    check("every column campaign_db writes exists in 001_schema.sql",
          not findings["unknown_column"],
          "; ".join(findings["unknown_column"]))
    check("every written value can land in the column it targets",
          not findings["type_drift"], "; ".join(findings["type_drift"]))
    check("the client's enum allowlists match the schema's enums",
          not findings["enum_drift"], "; ".join(findings["enum_drift"]))
    check("every table has a writer, or is a declared exception",
          not findings["writer_drift"], "; ".join(findings["writer_drift"]))

    # Without this, the audit could rot into UNKNOWN everywhere and keep
    # passing. UNKNOWN never fails a type check, so the share of columns that
    # resolve to a concrete family is the audit's real strength.
    resolved, total = schema_drift.resolution_report()
    check(f"the drift audit still resolves {resolved} of {total} written "
          "columns to a concrete type",
          total >= 60 and resolved >= total * 0.75,
          f"resolved {resolved} of {total}")


# ---------------------------------------------------------------------------
# 4. The product database is refused
# ---------------------------------------------------------------------------

def check_forbidden_project() -> None:
    import os
    saved = {k: os.environ.get(k) for k in ("SUPABASE_URL",
                                            "SUPABASE_SERVICE_KEY")}
    try:
        os.environ["SUPABASE_URL"] = "https://kngcxwcybozgqgnoweyt.supabase.co"
        os.environ["SUPABASE_SERVICE_KEY"] = "not-a-real-key"
        try:
            campaign_db._PostgrestClient.from_env()
            check("client refuses the product project ref", False,
                  "it built a client instead of raising")
        except campaign_db.CampaignDBError as exc:
            check("client refuses the product project ref",
                  "product database" in str(exc))
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    # Not even as a warning: a migration file is the thing that gets pasted
    # into a SQL editor, and the safest text to paste is text that does not
    # contain the forbidden ref at all.
    sql = "\n".join(p.read_text() for p in ROOT.rglob("*.sql"))
    check("the product project ref appears in no .sql file",
          campaign_db._FORBIDDEN_PROJECT_REF not in sql)


# ---------------------------------------------------------------------------
# 5. Seed + views + brief, end to end, and the idempotency proof
# ---------------------------------------------------------------------------

def check_seed_and_views() -> tuple[dict, dict, list]:
    client = sqlite_mirror.install()

    seed_demo.seed(verbose=False)
    first = client.dump()

    seed_demo.seed(verbose=False)
    second = client.dump()

    check("seed is idempotent - second run changes nothing",
          first == second, _first_difference(first, second))

    total = sum(len(rows) for rows in first.values())
    check(f"seed wrote rows ({total} across {len(first)} tables)", total > 50)

    markets = campaign_db.get_market_funnel()
    channels = campaign_db.get_channel_funnel()
    content = campaign_db.get_content_perf()

    check("v_market_funnel returns one row per market, always three",
          [m["market"] for m in markets] == ["dk", "lt", "global"],
          str([m["market"] for m in markets]))
    check("v_channel_funnel returns all four sources",
          [c["channel"] for c in channels] == ["outreach", "reel", "ad",
                                               "direct"])

    lt = next(m for m in markets if m["market"] == "lt")
    check("market funnel arithmetic: lt has 9 touches, 4 replies, 44.4% rate",
          (lt["touches_sent"], lt["replies"], lt["reply_rate_pct"])
          == (9, 4, 44.4), str(lt))

    # positive_replies = interested or referred. The seed gives lt two
    # interested, one not_now and one not_a_fit: 4 replies, 2 positive. dk has
    # one not_now and one objection: 2 replies, 0 positive, which is the case
    # that proves neutral values are counted as replies but not as positive.
    by_market = {m["market"]: m for m in markets}
    check("positive replies are interested + referred only (lt 2, dk 0, global 2)",
          tuple(by_market[k]["positive_replies"] for k in ("dk", "lt", "global"))
          == (0, 2, 2),
          str({k: v["positive_replies"] for k, v in by_market.items()}))
    check("neutral and negative values still count as replies (dk 2 of 8)",
          (by_market["dk"]["replies"], by_market["dk"]["touches_sent"]) == (2, 8),
          str(by_market["dk"]))
    outreach = next(c for c in channels if c["channel"] == "outreach")
    check("channel funnel agrees with market funnel on replies and positives",
          (outreach["replies"], outreach["positive_replies"]) == (10, 4),
          str(outreach))
    check("market funnel does not fan out on joins: 4 firms per market",
          all(m["companies_found"] == 4 for m in markets),
          str([m["companies_found"] for m in markets]))
    check("MRR only counts converted pilots (one, at 3197.00)",
          sum(float(m["mrr_eur"]) for m in markets) == 3197.00)

    ad = next(c for c in channels if c["channel"] == "ad")
    check("cost per booked call is computed for ads (178.55 / 2)",
          ad["cost_per_booked_call_eur"] == 89.28, str(ad))
    for c in channels:
        if c["channel"] != "ad":
            check(f"cost per booked call is NULL for {c['channel']}, not 0.00",
                  c["cost_per_booked_call_eur"] is None)

    check("v_content_perf uses only the latest snapshot per item per platform",
          all(c["stats_captured_on"] == "2026-09-22" for c in content),
          str(sorted({c["stats_captured_on"] for c in content})))
    rates = [c["engagement_rate_pct"] for c in content]
    check("v_content_perf is ordered by engagement, best first",
          rates == sorted(rates, reverse=True), str(rates))

    # The six-value taxonomy, exercised rather than merely declared.
    used = {s for _, _, _, s, _ in seed_demo.REPLIES}
    check("all six reply-sentiment values are exercised by the seed",
          used == {"interested", "not_now", "not_a_fit", "referred",
                   "objection", "unsubscribe"}, str(sorted(used)))

    # -- the guards, exercised rather than assumed ------------------------

    # Two probes: a plausible-sounding value that was never in the taxonomy,
    # and a real value in the wrong casing (the enum is case-sensitive and the
    # classifier writes lowercase). Both must fail before a request is built.
    for stale in ("positive", "INTERESTED"):
        try:
            campaign_db.record_reply("does-not-matter", "email", stale)
            check(f"record_reply rejects {stale!r} (outside the six values)",
                  False, f"it accepted {stale!r}")
        except campaign_db.CampaignDBError as exc:
            check(f"record_reply rejects {stale!r} (outside the six values)",
                  "interested" in str(exc) and "unsubscribe" in str(exc))

    # The routing CHECK: a 10+ seat team can never be filed as too_small, and a
    # 1-9 seat team can never be filed as anything else.
    import sqlite3
    try:
        campaign_db.insert_lead({
            "source": "ad", "market": "global", "locale": "en", "utm": {},
            "company_name": "Constraint Test", "work_email": "c@test.example",
            "team_size": "25-49", "email_client": "outlook",
            "role": "other", "submitted_at": "2026-09-20T00:00:00+00:00",
        }, stage="too_small")
        check("leads CHECK rejects a 25-49 seat team filed as too_small", False,
              "the row was accepted")
    except sqlite3.IntegrityError:
        check("leads CHECK rejects a 25-49 seat team filed as too_small", True)

    # A replayed touch must keep its original sent_at, not move to today.
    contact = client.select("contacts", limit=1)[0]
    original = client.select("touches", eq={"contact_id": contact["id"],
                                            "sequence_step": 1})[0]
    campaign_db.log_touch(contact["id"], original["channel"],
                          original["language"], sequence_step=1)
    after = client.select("touches", eq={"contact_id": contact["id"],
                                         "sequence_step": 1})[0]
    check("a replayed log_touch keeps the original sent_at",
          after["sent_at"] == original["sent_at"],
          f"{original['sent_at']} -> {after['sent_at']}")

    return markets, channels, content


def _first_difference(a: dict, b: dict) -> str:
    for table in a:
        if a[table] != b[table]:
            return (f"table {table}: {len(a[table])} rows then "
                    f"{len(b[table])} rows")
    return ""


def check_brief(markets, channels, content) -> None:
    text = friday_brief.render()
    for fragment in ("## The three markets", "## The channels", "## Content",
                     "## Ad spend against leads",
                     "## What the numbers suggest",
                     "## The numbers this rests on",
                     "Top three", "Bottom three"):
        check(f"brief contains {fragment!r}", fragment in text)

    check("brief names the weakest market plainly",
          "weakest market" in text)
    check("brief flags thin evidence rather than concluding",
          "hint, not a result" in text)
    check("brief ends with the numbers, not with advice",
          text.rstrip().endswith("```"))
    check("brief renders every market row",
          all(label in text for label in ("Denmark", "Lithuania",
                                          "US / global")))


# ---------------------------------------------------------------------------
# 6. No secrets, no em dashes, no AI-flavoured phrasing
# ---------------------------------------------------------------------------

SKIP_DIRS = {".git", "__pycache__", "briefs"}


def _committed_files() -> list[pathlib.Path]:
    return [p for p in ROOT.rglob("*")
            if p.is_file() and not any(d in p.parts for d in SKIP_DIRS)]


def check_no_secrets() -> None:
    jwt = re.compile(r"eyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{20,}")
    offenders = []
    for path in _committed_files():
        try:
            body = path.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        if jwt.search(body):
            offenders.append(str(path.relative_to(ROOT)))
        # A filled-in value on either variable, anywhere but the example file.
        if path.name != ".env.example":
            for match in re.finditer(
                    r"^(SUPABASE_SERVICE_KEY|SUPABASE_URL)=(.+)$", body,
                    re.M):
                if match.group(2).strip():
                    offenders.append(
                        f"{path.relative_to(ROOT)}: {match.group(1)} has a value")
    check("no secret value in any committed file", not offenders,
          str(offenders))

    example = (ROOT / ".env.example").read_text()
    check(".env.example ships with empty values",
          all(line.split("=", 1)[1].strip() in ("", '""')
              for line in example.splitlines()
              if line.strip() and not line.startswith("#") and "=" in line))


def check_voice() -> None:
    """House style, checked on the text a customer or Dovy actually reads."""
    banned = ("unlock", "supercharge", "in today's fast-paced", "seamless",
              "leverage", "game-changer", "revolutioni")
    text = friday_brief.render().lower()
    hits = [w for w in banned if w in text]
    check("no AI-flavoured phrasing in the rendered brief", not hits, str(hits))
    # Written as an escape so this file never contains the character it is
    # checking for, and so a global search-and-replace cannot quietly turn the
    # check into a tautology.
    em_dash = chr(0x2014)
    check("no em dash in the rendered brief", em_dash not in text)

    docs = [p for p in _committed_files()
            if p.suffix in (".md", ".sql", ".py", ".example")]
    dashed = [str(p.relative_to(ROOT)) for p in docs
              if em_dash in p.read_text(errors="ignore")]
    check("no em dash in any committed file", not dashed, str(dashed))


# ---------------------------------------------------------------------------

def main() -> int:
    print("campaign-ledger QA gate - no database connection is made\n")
    check_migrations_parse()
    check_rls()
    check_client_surface()
    check_schema_drift()
    check_forbidden_project()
    markets, channels, content = check_seed_and_views()
    check_brief(markets, channels, content)
    check_no_secrets()
    check_voice()

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for line in FAILED:
        print(f"  FAILED: {line}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
