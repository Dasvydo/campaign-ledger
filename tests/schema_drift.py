#!/usr/bin/env python3
"""Does every column `src/campaign_db.py` writes exist in the schema, with a
compatible type?

    python3 tests/schema_drift.py        # standalone, prints the full map

Why this exists
---------------
The other five batches import `campaign_db` and fall back to a local JSONL shim
when it is absent. A column that the client writes but the schema does not have,
or has with an incompatible type, therefore passes every offline test in every
repo and fails on the first live write. Batch E hit exactly that on 2026-09-08:
a slug was being passed into a `uuid` column.

Nothing here connects to anything. The schema side is parsed out of
`migrations/001_schema.sql` with sqlglot; the client side is read out of
`src/campaign_db.py` with `ast`. Neither file is imported or executed, so the
audit is a property of the committed text, not of a particular run.

Three drift classes are checked:

  1. unknown column  - the client writes a column the table does not have
  2. type drift      - it writes a value whose Python type family cannot land in
                       that column's Postgres type
  3. enum drift      - a `_check` allowlist in the client disagrees with the
                       `create type ... as enum` it guards

Type families are deliberately coarse. The question is not "is this the exact
Postgres type", it is "could this value possibly be that column". The one
distinction that carries weight is TEXT_ID versus TEXT_MADE: a bare `str`
parameter whose name ends in `_id` may land in a `uuid` column, a string the
function *built* (a slug, an f-string, a normalised domain) may not. That is the
2026-09-08 bug, stated as a rule.
"""
from __future__ import annotations

import ast
import pathlib
import re
from typing import Any

import sqlglot
from sqlglot import exp

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCHEMA_SQL = ROOT / "migrations" / "001_schema.sql"
CLIENT_PY = ROOT / "src" / "campaign_db.py"

# `meetings` and `pilots` have no writer in campaign_db and are expected not to.
# The eleven exported functions are the ones the other batches need, and no
# engine writes a meeting: the seed reaches the private client directly rather
# than widening the public surface for test data. Recorded here as an explicit
# expectation so that adding a writer, or losing one on another table, fails
# this audit instead of passing silently. See SESSION-REPORT.md, "Found".
WRITERLESS_TABLES = {"meetings", "pilots"}


# ---------------------------------------------------------------------------
# The schema side
# ---------------------------------------------------------------------------

def schema_columns() -> dict[str, dict[str, str]]:
    """{table: {column: postgres type}} for every campaign.* table."""
    tables: dict[str, dict[str, str]] = {}
    for statement in sqlglot.parse(SCHEMA_SQL.read_text(), dialect="postgres"):
        if not isinstance(statement, exp.Create) or statement.kind != "TABLE":
            continue
        name = statement.this.this.this.name
        tables[name] = {
            column.name: column.args["kind"].sql(dialect="postgres")
            for column in statement.find_all(exp.ColumnDef)
        }
    return tables


_ENUM_RE = re.compile(
    r"create\s+type\s+campaign\.(\w+)\s+as\s+enum\s*\((.*?)\)\s*;",
    re.S | re.I)


def schema_enums() -> dict[str, tuple[str, ...]]:
    """{enum name: values}, read out of the guarded CREATE TYPE blocks."""
    text = SCHEMA_SQL.read_text()
    return {
        name: tuple(re.findall(r"'([^']*)'", body))
        for name, body in _ENUM_RE.findall(text)
    }


# ---------------------------------------------------------------------------
# Type families
# ---------------------------------------------------------------------------
# What a column can hold.
SQL_FAMILY = [
    (r"^UUID$", "uuid"),
    (r"^TEXT\[\]$|^VARCHAR.*\[\]$", "array"),
    (r"^TEXT$|^VARCHAR|^CHAR", "text"),
    (r"^campaign\.", "text"),          # every enum in this schema is textual
    (r"^INT$|^INTEGER$|^BIGINT$|^SMALLINT$", "int"),
    (r"^DECIMAL|^NUMERIC", "num"),
    (r"^DOUBLE|^REAL|^FLOAT", "num"),
    (r"^BOOLEAN$|^BOOL$", "bool"),
    (r"^DATE$", "date"),
    (r"^TIMESTAMPTZ$|^TIMESTAMP", "ts"),
]

# What a written value is.
#   TEXT_ID    a bare `str` parameter named like an id - may be a uuid
#   TEXT_MADE  a string this function constructed - definitely not a uuid
#   TEXT_PARAM a bare `str` parameter that is not an id
#   UNKNOWN    not resolvable from the source; never fails a check
ACCEPTS = {
    "uuid":  {"TEXT_ID", "UNKNOWN"},
    "text":  {"TEXT_ID", "TEXT_MADE", "TEXT_PARAM", "UNKNOWN"},
    "int":   {"INT", "UNKNOWN"},
    "num":   {"NUM", "INT", "UNKNOWN"},
    "bool":  {"BOOL", "UNKNOWN"},
    "date":  {"DATE", "UNKNOWN"},
    "ts":    {"TS", "UNKNOWN"},
    "array": {"ARRAY", "UNKNOWN"},
}


def sql_family(sql_type: str) -> str:
    for pattern, family in SQL_FAMILY:
        if re.match(pattern, sql_type, re.I):
            return family
    return "unknown"


# Helpers in campaign_db whose return family is fixed and known.
_HELPER_FAMILY = {
    "_iso": "TS",
    "_day": "DATE",
    "_slug": "TEXT_MADE",
    "_normalise_domain": "TEXT_MADE",
    "_normalise_linkedin": "TEXT_MADE",
    "_route_stage": "TEXT_MADE",
    "int": "INT",
    "float": "NUM",
    "round": "NUM",
    "str": "TEXT_MADE",
    "list": "ARRAY",
    "bool": "BOOL",
}

_ANNOTATION_FAMILY = {
    "str": "TEXT_PARAM",
    "int": "INT",
    "float": "NUM",
    "bool": "BOOL",
}


# ---------------------------------------------------------------------------
# The client side
# ---------------------------------------------------------------------------

class _FunctionScope:
    """Parameter annotations and local dict/value bindings for one function."""

    def __init__(self, node: ast.FunctionDef) -> None:
        self.node = node
        self.params: dict[str, ast.expr | None] = {}
        args = node.args
        for arg in list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs):
            self.params[arg.arg] = arg.annotation
        self.locals: dict[str, ast.expr] = {}
        for statement in ast.walk(node):
            if isinstance(statement, ast.Assign):
                for target in statement.targets:
                    if isinstance(target, ast.Name):
                        self.locals.setdefault(target.id, statement.value)


def _unwrap(node: ast.expr) -> ast.expr:
    """Strip `_clean(...)` and `_one(...)`, which pass their argument through."""
    while (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
           and node.func.id in ("_clean", "_one") and node.args):
        node = node.args[0]
    return node


def _resolve_dict(node: ast.expr, scope: _FunctionScope,
                  depth: int = 0) -> ast.Dict | None:
    """Follow a name back to the dict literal it was bound to."""
    if depth > 4:
        return None
    node = _unwrap(node)
    if isinstance(node, ast.Dict):
        return node
    if isinstance(node, ast.List) and len(node.elts) == 1:
        return _resolve_dict(node.elts[0], scope, depth + 1)
    if isinstance(node, ast.Name) and node.id in scope.locals:
        return _resolve_dict(scope.locals[node.id], scope, depth + 1)
    if isinstance(node, ast.DictComp):
        return None
    return None


def _family_of(node: ast.expr, scope: _FunctionScope, depth: int = 0) -> str:
    """The type family of one written value, read off the source expression."""
    if depth > 6:
        return "UNKNOWN"
    node = _unwrap(node)

    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool):
            return "BOOL"
        if isinstance(node.value, int):
            return "INT"
        if isinstance(node.value, float):
            return "NUM"
        if isinstance(node.value, str):
            return "TEXT_MADE"
        return "UNKNOWN"

    if isinstance(node, ast.JoinedStr):          # f-string
        return "TEXT_MADE"

    if isinstance(node, ast.List):
        return "ARRAY"

    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name):
            name = node.func.id
            if name == "_check" and node.args:   # returns its first argument
                return _family_of(node.args[0], scope, depth + 1)
            if name in _HELPER_FAMILY:
                return _HELPER_FAMILY[name]
            return "UNKNOWN"
        if isinstance(node.func, ast.Attribute):
            # `something.strip()` / `.lower()` are strings this code made;
            # `payload.get("x")` could be anything.
            if node.func.attr in ("strip", "lower", "upper", "rstrip", "join"):
                return "TEXT_MADE"
            return "UNKNOWN"
        return "UNKNOWN"

    if isinstance(node, ast.BinOp):
        left = _family_of(node.left, scope, depth + 1)
        right = _family_of(node.right, scope, depth + 1)
        if "TEXT_MADE" in (left, right):
            return "TEXT_MADE"
        return left if left == right else "UNKNOWN"

    if isinstance(node, (ast.BoolOp, ast.IfExp)):
        branches = (node.values if isinstance(node, ast.BoolOp)
                    else [node.body, node.orelse])
        seen = {f for f in (_family_of(b, scope, depth + 1) for b in branches)
                if f != "UNKNOWN"}
        # `a or b or c`: any constructed string makes the whole thing one.
        if "TEXT_MADE" in seen:
            return "TEXT_MADE"
        return seen.pop() if len(seen) == 1 else "UNKNOWN"

    if isinstance(node, ast.Name):
        if node.id in scope.locals:
            return _family_of(scope.locals[node.id], scope, depth + 1)
        if node.id in scope.params:
            return _annotation_family(node.id, scope.params[node.id])
        return "UNKNOWN"

    return "UNKNOWN"


def _annotation_family(param_name: str, annotation: ast.expr | None) -> str:
    if annotation is None:
        return "UNKNOWN"
    text = ast.unparse(annotation)
    # `str | None` and `Optional[str]` are the same question as `str`.
    text = text.replace("| None", "").replace("Optional[", "").strip(" []")
    if text.startswith(("Sequence", "list", "List", "tuple")):
        return "ARRAY"
    base = text.split("[")[0].strip()
    family = _ANNOTATION_FAMILY.get(base, "UNKNOWN")
    if family == "TEXT_PARAM" and param_name.endswith("_id"):
        return "TEXT_ID"
    return family


def client_writes() -> dict[str, dict[str, tuple[str, str, int]]]:
    """{table: {column: (family, writing function, line)}}.

    Every `.upsert(...)` and `.update(...)` in campaign_db, resolved back to the
    dict literal whose keys are the columns being written.
    """
    tree = ast.parse(CLIENT_PY.read_text())
    writes: dict[str, dict[str, tuple[str, str, int]]] = {}

    for function in ast.walk(tree):
        if not isinstance(function, ast.FunctionDef):
            continue
        scope = _FunctionScope(function)
        for call in ast.walk(function):
            if not isinstance(call, ast.Call):
                continue
            if not isinstance(call.func, ast.Attribute):
                continue
            if call.func.attr not in ("upsert", "update"):
                continue
            if not call.args or not isinstance(call.args[0], ast.Constant):
                continue
            table = call.args[0].value
            if not isinstance(table, str) or len(call.args) < 2:
                continue
            payload = _resolve_dict(call.args[1], scope)
            if payload is None:
                continue
            for key, value in zip(payload.keys, payload.values):
                if not (isinstance(key, ast.Constant)
                        and isinstance(key.value, str)):
                    continue
                writes.setdefault(table, {})[key.value] = (
                    _family_of(value, scope), function.name, call.lineno)
    return writes


def client_enums() -> dict[str, tuple[str, ...]]:
    """The module-level `_CHECK` allowlists, read without importing the module."""
    tree = ast.parse(CLIENT_PY.read_text())
    found: dict[str, tuple[str, ...]] = {}
    for statement in tree.body:
        if not isinstance(statement, ast.Assign):
            continue
        target = statement.targets[0]
        if not (isinstance(target, ast.Name) and target.id.isupper()
                and target.id.startswith("_")):
            continue
        if not isinstance(statement.value, ast.Tuple):
            continue
        values = [e.value for e in statement.value.elts
                  if isinstance(e, ast.Constant) and isinstance(e.value, str)]
        if values and len(values) == len(statement.value.elts):
            found[target.id] = tuple(values)
    return found


# The client tuple that guards each schema enum. Both sides are read from the
# committed text, so this mapping is the only hand-written part of the audit.
ENUM_PAIRS = {
    "market": "_MARKETS",
    "lang": "_LANGS",
    "segment": "_SEGMENTS",
    "company_source": "_COMPANY_SOURCES",
    "email_source": "_EMAIL_SOURCES",
    "touch_channel": "_TOUCH_CHANNELS",
    "reply_sentiment": "_SENTIMENTS",
    "team_size": "_TEAM_SIZES",
    "email_client": "_EMAIL_CLIENTS",
    "lead_role": "_LEAD_ROLES",
    "lead_source": "_LEAD_SOURCES",
    "lead_stage": "_LEAD_STAGES",
    "content_kind": "_CONTENT_KINDS",
    "content_lane": "_CONTENT_LANES",
}


# ---------------------------------------------------------------------------
# The audit
# ---------------------------------------------------------------------------

def audit() -> dict[str, list[str]]:
    """Every drift found, grouped by class. Empty lists mean no drift."""
    tables = schema_columns()
    writes = client_writes()
    enums = schema_enums()
    allowlists = client_enums()

    unknown_column: list[str] = []
    type_drift: list[str] = []
    enum_drift: list[str] = []
    writer_drift: list[str] = []

    for table, columns in sorted(writes.items()):
        if table not in tables:
            unknown_column.append(f"{table}: no such table in the schema")
            continue
        for column, (family, function, line) in sorted(columns.items()):
            if column not in tables[table]:
                unknown_column.append(
                    f"{table}.{column} written by {function}() at "
                    f"campaign_db.py:{line}, but the schema has no such column")
                continue
            sql_type = tables[table][column]
            target = sql_family(sql_type)
            if target == "unknown":
                continue
            if family not in ACCEPTS[target]:
                type_drift.append(
                    f"{table}.{column} is {sql_type} but {function}() writes a "
                    f"{family} value (campaign_db.py:{line})")

    written_tables = set(writes)
    for table in sorted(set(tables) - written_tables - WRITERLESS_TABLES):
        writer_drift.append(f"{table} has no writer in campaign_db")
    for table in sorted(WRITERLESS_TABLES & written_tables):
        writer_drift.append(
            f"{table} now has a writer in campaign_db - it is listed in "
            "WRITERLESS_TABLES as having none, so that list is stale")
    for table in sorted(WRITERLESS_TABLES - set(tables)):
        writer_drift.append(
            f"{table} is in WRITERLESS_TABLES but is not a table in the schema")

    for enum_name, constant in sorted(ENUM_PAIRS.items()):
        if enum_name not in enums:
            enum_drift.append(f"schema has no enum campaign.{enum_name}")
            continue
        if constant not in allowlists:
            enum_drift.append(f"campaign_db has no allowlist {constant}")
            continue
        schema_values = set(enums[enum_name])
        client_values = set(allowlists[constant])
        if schema_values != client_values:
            enum_drift.append(
                f"campaign.{enum_name} vs {constant}: "
                f"client-only {sorted(client_values - schema_values)}, "
                f"schema-only {sorted(schema_values - client_values)}")

    return {
        "unknown_column": unknown_column,
        "type_drift": type_drift,
        "enum_drift": enum_drift,
        "writer_drift": writer_drift,
    }


def resolution_report() -> tuple[int, int]:
    """(columns whose family was resolved, columns written). Guards against the
    audit quietly degrading into UNKNOWN everywhere, which would pass forever."""
    writes = client_writes()
    total = sum(len(c) for c in writes.values())
    resolved = sum(1 for columns in writes.values()
                   for family, _, _ in columns.values() if family != "UNKNOWN")
    return resolved, total


def main() -> int:
    tables = schema_columns()
    writes = client_writes()
    print("campaign-ledger schema drift audit - nothing is imported or connected\n")
    for table in sorted(tables):
        columns = writes.get(table, {})
        if not columns:
            note = ("expected: no writer"
                    if table in WRITERLESS_TABLES else "NO WRITER")
            print(f"{table:15} ({note})")
            continue
        print(f"{table}")
        for column, (family, function, line) in sorted(columns.items()):
            sql_type = tables[table].get(column, "MISSING")
            print(f"    {column:20} {sql_type:25} <- {family:10} {function}()")
        print()

    resolved, total = resolution_report()
    print(f"{resolved}/{total} written columns resolved to a concrete type "
          f"family, {total - resolved} unknown")

    findings = audit()
    print()
    for name, items in findings.items():
        print(f"{name}: {len(items)}")
        for item in items:
            print(f"    {item}")
    return 1 if any(findings.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
