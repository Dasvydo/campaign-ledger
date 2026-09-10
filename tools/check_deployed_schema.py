#!/usr/bin/env python3
"""Diff the DEPLOYED campaign schema against the one committed here.

`tests/schema_drift.py` compares `src/campaign_db.py` against
`migrations/001_schema.sql` - two files in this repository. That catches a
client that writes a column the schema never declared, but it is blind to the
thing that actually bites: whether the migration was ever applied, and whether
somebody later edited the live database in the SQL editor. A schema that was
never run and a schema that was quietly altered both read as clean.

This closes that, without a credential ever reaching this repository. You run
one read-only query in the Supabase SQL editor, paste the result into a file,
and this diffs it. Nothing here connects to anything.

  1. Supabase -> your campaign project -> SQL Editor. CHECK THE PROJECT REF in
     the URL first: this must be the campaign database, never the product one.

  2. Run:

        select table_name, column_name, data_type
        from information_schema.columns
        where table_schema = 'campaign'
        order by table_name, ordinal_position;

  3. Export or copy the result, save it beside this repo, and run:

        python3 tools/check_deployed_schema.py deployed.csv

It accepts the CSV Supabase's "Download CSV" button produces, and also plain
pasted text with any of comma, tab or pipe between the three fields.

Exit 0 means the deployed schema has every table and column the committed
migration declares. Exit 1 lists what differs, in both directions: a column the
database is missing (the migration did not fully apply), and a column the
database has that the migration never declared (someone edited it live).

Types are reported but never failed on: information_schema spells them
differently from the DDL (`timestamp with time zone` against `timestamptz`,
`USER-DEFINED` for every enum), and normalising that mapping would be a second
source of truth to keep in step. Presence is what this proves.
"""
from __future__ import annotations

import logging
import re
import sys
from pathlib import Path

# 001_schema.sql wraps every CREATE TYPE in a guarded DO block, which sqlglot
# parses as an opaque Command and says so, loudly, once per block. That is
# expected and harmless - schema_columns() only reads CREATE TABLE - but thirty
# warnings above a clean result reads like a failure.
logging.getLogger("sqlglot").setLevel(logging.ERROR)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

from schema_drift import schema_columns                       # noqa: E402

SPLIT = re.compile(r"\s*[,\t|]\s*")


def parse(text: str) -> dict[str, dict[str, str]]:
    """{table: {column: type}} out of whatever the SQL editor gave you."""
    found: dict[str, dict[str, str]] = {}
    for line in text.splitlines():
        line = line.strip().strip("|").strip()
        if not line:
            continue
        parts = [p.strip().strip('"').strip("'") for p in SPLIT.split(line)]
        if len(parts) < 3:
            continue
        table, column, kind = parts[0], parts[1], parts[2]
        if table in ("table_name", "") or column in ("column_name", ""):
            continue        # the header row, however it was pasted
        if set(table) <= set("-+ "):
            continue        # a psql rule line
        found.setdefault(table, {})[column] = kind
    return found


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(__doc__)
        return 2

    path = Path(argv[0])
    if not path.is_file():
        print(f"no such file: {path}")
        return 2

    deployed = parse(path.read_text(encoding="utf-8"))
    committed = schema_columns()

    if not deployed:
        print("nothing parsed out of that file. Expected three columns per row:\n"
              "  table_name, column_name, data_type\n"
              "Paste the query result, not the query.")
        return 2

    problems: list[str] = []

    missing_tables = sorted(set(committed) - set(deployed))
    for table in missing_tables:
        problems.append(
            f"campaign.{table} is declared in 001_schema.sql and is NOT in the "
            f"database. The migration did not fully apply.")

    extra_tables = sorted(set(deployed) - set(committed))
    for table in extra_tables:
        problems.append(
            f"campaign.{table} exists in the database and is declared nowhere "
            f"in this repo. Somebody created it outside the migrations.")

    for table in sorted(set(committed) & set(deployed)):
        want, have = committed[table], deployed[table]
        for column in sorted(set(want) - set(have)):
            problems.append(
                f"campaign.{table}.{column} is declared ({want[column]}) and is "
                f"missing from the database")
        for column in sorted(set(have) - set(want)):
            problems.append(
                f"campaign.{table}.{column} exists in the database ({have[column]}) "
                f"and is declared nowhere in this repo")

    tables = len(set(committed) & set(deployed))
    columns = sum(len(set(committed[t]) & set(deployed[t]))
                  for t in set(committed) & set(deployed))
    print(f"committed: {len(committed)} tables. deployed: {len(deployed)} tables. "
          f"matched: {tables} tables, {columns} columns.\n")

    if problems:
        print(f"{len(problems)} difference(s):\n")
        for p in problems:
            print(f"  - {p}")
        return 1

    print("the deployed schema carries every table and column the committed "
          "migration declares.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
