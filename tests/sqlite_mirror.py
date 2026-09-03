"""A SQLite mirror of the ledger, built from the committed migration files.

Why this exists
---------------
No database can be reached from the build session, and connecting to one is
forbidden anyway. But "the views work" and "the seed is idempotent" are claims
that need evidence, not assertion.

So this module takes `migrations/001_schema.sql` and `migrations/003_views.sql`
- the actual committed files, not a hand-written copy - mechanically rewrites
the handful of constructs SQLite does not share with Postgres, and runs them in
an in-memory SQLite database. It then presents the same four-method interface
`campaign_db` expects from its PostgREST client, so the real client code, the
real seed and the real Friday brief all run unmodified on top of it.

What that does and does not prove
---------------------------------
Proves: the column references in the three views resolve, the join and
aggregate logic returns the numbers it should, the CHECK constraints and UNIQUE
constraints fire, and `seed_demo.py` is genuinely idempotent.

Does not prove: enum behaviour (SQLite has no enums, so the columns are plain
text and only the client-side `_check()` guards them), RLS (SQLite has none),
generated columns, or that Postgres agrees on every rounding decision. Those
are listed as unverified in RUN-REPORT.md.

The rewrites below are deliberately few and each one is listed, so the gap
between what ran here and what will run in Postgres stays inspectable.
"""
from __future__ import annotations

import json
import pathlib
import re
import sqlite3
import uuid
from typing import Any, Mapping, Sequence

ROOT = pathlib.Path(__file__).resolve().parent.parent

# The 15 enum types from 001_schema.sql. In the mirror they become plain TEXT.
ENUMS = (
    "market", "segment", "company_source", "email_source", "touch_channel",
    "lang", "reply_sentiment", "team_size", "email_client", "lead_role",
    "lead_source", "lead_stage", "meeting_status", "content_kind",
    "content_lane",
)


def _strip_comments(sql: str) -> str:
    return "\n".join(re.sub(r"--.*$", "", line) for line in sql.splitlines())


def to_sqlite(sql: str) -> str:
    """Rewrite Postgres DDL into the SQLite dialect. Every change is listed."""
    sql = _strip_comments(sql)

    # 1. PL/pgSQL DO blocks (the guarded CREATE TYPE enums, and the RLS loop).
    sql = re.sub(r"do \$\$.*?\$\$\s*;", "", sql, flags=re.S | re.I)
    # 2. COMMENT ON, CREATE SCHEMA, and the privilege statements.
    # (the string literal is matched explicitly: a comment body may itself
    #  contain a semicolon, and a lazy .*?; would cut the statement in half)
    sql = re.sub(r"comment on .*?is\s+'(?:[^']|'')*'\s*;", "", sql,
                 flags=re.S | re.I)
    sql = re.sub(r"create schema[^;]*;", "", sql, flags=re.I)
    sql = re.sub(r"^\s*(grant|revoke|alter default privileges)[^;]*;", "", sql,
                 flags=re.I | re.M)
    sql = re.sub(r"alter table [^;]*enable row level security\s*;", "", sql,
                 flags=re.I)
    # 3. Enum types -> TEXT (done before the schema prefix is stripped, so
    #    `campaign.market` the TYPE is not confused with `market` the COLUMN).
    for name in ENUMS:
        sql = re.sub(rf"\bcampaign\.{name}\b", "text", sql)
    # 4. Everything else in the schema is unqualified in SQLite.
    sql = sql.replace("campaign.", "")
    # 5. Types SQLite does not have.
    sql = sql.replace("timestamptz", "text")
    sql = sql.replace("text[]", "text")
    sql = re.sub(r"\bnumeric\(\d+,\s*\d+\)", "real", sql)
    sql = re.sub(r"\buuid\b", "text", sql)
    sql = re.sub(r"\bdate\b(?=\s|,|\))", "text", sql)
    # 6. Server-side defaults.
    sql = sql.replace("default gen_random_uuid()",
                      "default (lower(hex(randomblob(16))))")
    sql = sql.replace("default now()", "default (datetime('now'))")
    # 7. The one generated column. `started_on + 14` is date arithmetic in
    #    Postgres and string concatenation in SQLite, so the mirror computes it
    #    with julianday instead of dropping it.
    sql = re.sub(
        r"generated always as \(started_on \+ 14\) stored",
        "generated always as (date(started_on, '+14 days')) stored", sql)
    # 8. Views.
    sql = re.sub(r"create or replace view", "create view", sql, flags=re.I)
    sql = sql.replace("(current_date - cast(c.published_at as text))",
                      "cast(julianday('now') - julianday(c.published_at) as integer)")
    return sql


def build(connection: sqlite3.Connection) -> None:
    for name in ("001_schema.sql", "003_views.sql"):
        connection.executescript(
            to_sqlite((ROOT / "migrations" / name).read_text()))
    connection.commit()


class SqliteMirrorClient:
    """Stands in for `campaign_db._PostgrestClient`, same four methods."""

    def __init__(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("pragma foreign_keys = on")
        build(self.connection)

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _bind(value: Any) -> Any:
        """SQLite has no array type. PostgREST sends a JSON list and Postgres
        stores it in `text[]`; the mirror stores the JSON, which is enough for
        round-tripping and for the idempotency diff."""
        if isinstance(value, (list, tuple)):
            return json.dumps(list(value))
        if isinstance(value, bool):
            return int(value)
        return value

    def _fetch(self, sql: str, args: Sequence[Any] = ()) -> list[dict[str, Any]]:
        cur = self.connection.execute(sql, tuple(self._bind(a) for a in args))
        return [dict(r) for r in cur.fetchall()]

    def _columns(self, table: str) -> list[str]:
        return [r["name"] for r in self._fetch(f"pragma table_info({table})")]

    # -- the interface campaign_db calls ------------------------------------

    def upsert(self, table: str, rows: Sequence[Mapping[str, Any]],
               on_conflict: str, *, ignore_duplicates: bool = False
               ) -> list[dict[str, Any]]:
        keys = [k.strip() for k in on_conflict.split(",")]
        out: list[dict[str, Any]] = []
        for row in rows:
            row = dict(row)
            where = " and ".join(f"{k} = ?" for k in keys)
            existing = self._fetch(
                f"select * from {table} where {where}", [row[k] for k in keys])

            if existing and ignore_duplicates:
                continue                      # PostgREST returns no row either
            if existing:
                sets = [c for c in row if c not in keys]
                if sets:
                    self.connection.execute(
                        f"update {table} set "
                        + ", ".join(f"{c} = ?" for c in sets)
                        + f" where {where}",
                        [self._bind(row[c]) for c in sets]
                        + [self._bind(row[k]) for k in keys])
                out.extend(self._fetch(
                    f"select * from {table} where {where}",
                    [row[k] for k in keys]))
            else:
                if "id" in self._columns(table) and "id" not in row:
                    row["id"] = str(uuid.uuid4())
                cols = list(row)
                self.connection.execute(
                    f"insert into {table} ({', '.join(cols)}) values "
                    f"({', '.join('?' for _ in cols)})",
                    [self._bind(row[c]) for c in cols])
                out.extend(self._fetch(
                    f"select * from {table} where {where}",
                    [row[k] for k in keys]))
        self.connection.commit()
        return out

    def update(self, table: str, values: Mapping[str, Any],
               eq: Mapping[str, Any]) -> list[dict[str, Any]]:
        where = " and ".join(f"{k} = ?" for k in eq)
        self.connection.execute(
            f"update {table} set " + ", ".join(f"{c} = ?" for c in values)
            + f" where {where}",
            [self._bind(v) for v in values.values()]
            + [self._bind(v) for v in eq.values()])
        self.connection.commit()
        return self._fetch(f"select * from {table} where {where}",
                           list(eq.values()))

    def select(self, relation: str, *, eq: Mapping[str, Any] | None = None,
               order: str | None = None, limit: int | None = None
               ) -> list[dict[str, Any]]:
        sql = f"select * from {relation}"
        args: list[Any] = []
        if eq:
            sql += " where " + " and ".join(f"{k} = ?" for k in eq)
            args = list(eq.values())
        if order:                                   # "sort_order.asc"
            column, _, direction = order.partition(".")
            sql += f" order by {column} {direction or 'asc'}"
        if limit:
            sql += f" limit {int(limit)}"
        return self._fetch(sql, args)

    # -- for the idempotency proof -----------------------------------------

    def dump(self) -> dict[str, list[dict[str, Any]]]:
        """Every row in every table, deterministically ordered."""
        tables = [r["name"] for r in self._fetch(
            "select name from sqlite_master where type='table' "
            "and name not like 'sqlite_%' order by name")]
        return {t: sorted(self._fetch(f"select * from {t}"),
                          key=lambda r: repr(sorted(r.items())))
                for t in tables}


def install() -> SqliteMirrorClient:
    """Point `campaign_db` at a fresh in-memory mirror and return it."""
    import campaign_db

    client = SqliteMirrorClient()
    campaign_db._CLIENT = client
    return client
