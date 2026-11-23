import difflib
import re
from collections.abc import Iterable

import sqlglot
from sqlglot import exp
from sqlglot.errors import OptimizeError, SqlglotError
from sqlglot.optimizer.qualify import qualify

from datalyst.core.models import DataSchema

DEFAULT_SCHEMA = "public"
_UNRESOLVED = re.compile(
    r"(?:Column '?\"?([\w.]+)\"?'? could not be resolved"
    r"|Unknown column: \"?([\w.]+))",
    re.IGNORECASE,
)


def referenced_tables(
    sql: str, dialect: str = "postgres"
) -> list[tuple[str | None, str]]:
    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except SqlglotError:
        return []
    ctes = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    refs: dict[tuple[str | None, str], None] = {}
    for table in tree.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            continue
        schema = table.db or None
        if schema is None and table.name.lower() in ctes:
            continue
        refs.setdefault((schema, table.name))
    return list(refs)


def resolve_tables(
    refs: Iterable[tuple[str | None, str]], schema: DataSchema
) -> tuple[list[str], list[str]]:
    by_full = {t.qualified_name.lower(): t.qualified_name for t in schema.tables}
    by_name: dict[str, list[str]] = {}
    for table in schema.tables:
        by_name.setdefault(table.name.lower(), []).append(table.qualified_name)
    found, missing = [], []
    for schema_name, name in refs:
        if schema_name:
            match = by_full.get(f"{schema_name}.{name}".lower())
        else:
            options = by_name.get(name.lower(), [])
            preferred = [o for o in options if o.startswith(f"{DEFAULT_SCHEMA}.")]
            match = (
                (preferred or options or [None])[0]
                if len(options) <= 1 or preferred
                else None
            )
        if match:
            found.append(match)
        else:
            missing.append(f"{schema_name}.{name}" if schema_name else name)
    return list(dict.fromkeys(found)), missing


def tables_in_sql(sql: str, schema: DataSchema, dialect: str = "postgres") -> list[str]:
    return resolve_tables(referenced_tables(sql, dialect), schema)[0]


def catalog_problems(
    sql: str, schema: DataSchema, dialect: str = "postgres"
) -> list[str]:
    refs = referenced_tables(sql, dialect)
    found, missing = resolve_tables(refs, schema)
    names = [t.qualified_name for t in schema.tables]
    problems = [
        f'Table "{name}" does not exist.{_suggest(name.split(".")[-1], names)}'
        for name in missing
    ]
    if problems:
        return problems
    tables = [t for t in schema.tables if t.qualified_name in set(found)]
    mapping: dict[str, dict[str, dict[str, str]]] = {}
    for table in tables:
        mapping.setdefault(table.schema_name, {})[table.name] = {
            c.name: "TEXT" for c in table.columns
        }
    try:
        tree = sqlglot.parse_one(sql, read=dialect)
        qualify(
            tree,
            schema=mapping,
            dialect=dialect,
            db=DEFAULT_SCHEMA,
            validate_qualify_columns=True,
            quote_identifiers=False,
        )
    except OptimizeError as e:
        match = _UNRESOLVED.search(str(e))
        if not match:
            return []
        column = (match.group(1) or match.group(2)).split(".")[-1]
        known = [f"{t.name}.{c.name}" for t in tables for c in t.columns]
        hint = _suggest(column, [c.name for t in tables for c in t.columns], known)
        return [f'Column "{column}" does not exist in the tables used.{hint}']
    except SqlglotError:
        return []
    return []


def _suggest(name: str, candidates: list[str], labels: list[str] | None = None) -> str:
    labels = labels or candidates
    lowered = [c.lower() for c in candidates]
    close = difflib.get_close_matches(name.lower(), lowered, n=3, cutoff=0.5)
    if not close:
        return ""
    picked = [labels[lowered.index(c)] for c in close]
    return " Did you mean: " + ", ".join(dict.fromkeys(picked)) + "?"
