#!/usr/bin/env python3
"""Parse-check the migrations without connecting to any database.

    python3 tools/validate_sql.py

What this proves, and what it does not
--------------------------------------
sqlglot parses each file with its Postgres dialect. Statements it fully
understands become a typed AST - a malformed CREATE TABLE, a missing comma, an
unbalanced paren or a bad view expression all fail here.

Statements it does not model (PL/pgSQL `DO $$ ... $$` blocks, `ALTER TABLE ...
ENABLE ROW LEVEL SECURITY`, `GRANT/REVOKE ... ON ALL TABLES IN SCHEMA`) are kept
as opaque `Command` nodes. Those are NOT syntax-checked. This script counts and
lists them so the claim in RUN-REPORT.md stays honest.

Nothing here connects to a database, and nothing here checks semantics: a
reference to a column that does not exist parses perfectly well. The SQLite
mirror in tests/ is what exercises the column references and the view logic.
"""
from __future__ import annotations

import pathlib
import sys
import warnings

import sqlglot
from sqlglot import exp

warnings.filterwarnings("ignore")
sqlglot.logger.setLevel("ERROR")

ROOT = pathlib.Path(__file__).resolve().parent.parent
FILES = ["001_schema.sql", "002_rls.sql", "003_views.sql"]


def main() -> int:
    failures = 0
    total_parsed = 0
    total_opaque = 0

    for name in FILES:
        path = ROOT / "migrations" / name
        try:
            tree = sqlglot.parse(path.read_text(), read="postgres")
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL  {name}\n      {exc}")
            failures += 1
            continue

        statements = [s for s in tree if s is not None]
        opaque = [s for s in statements if isinstance(s, exp.Command)]
        modelled = len(statements) - len(opaque)
        total_parsed += modelled
        total_opaque += len(opaque)

        print(f"ok    {name}: {len(statements)} statements "
              f"({modelled} fully parsed, {len(opaque)} opaque)")
        if opaque:
            kinds: dict[str, int] = {}
            for s in opaque:
                head = " ".join(str(s).split()[:4]).lower()
                for label in ("do $$", "alter table", "grant", "revoke",
                              "alter default", "comment on"):
                    if head.startswith(label):
                        kinds[label] = kinds.get(label, 0) + 1
                        break
                else:
                    kinds[head[:24]] = kinds.get(head[:24], 0) + 1
            summary = ", ".join(f"{k} x{v}" for k, v in sorted(kinds.items()))
            print(f"        not syntax-checked: {summary}")

    print()
    print(f"sqlglot {sqlglot.__version__}, postgres dialect, parse only. "
          f"No connection was made.")
    print(f"{total_parsed} statements fully parsed, {total_opaque} opaque "
          f"(PL/pgSQL blocks and privilege statements sqlglot does not model).")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
