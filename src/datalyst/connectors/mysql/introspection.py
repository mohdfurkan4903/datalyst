import hashlib
import re
from collections.abc import Iterable, Sequence
from typing import Any

from datalyst.core.models import (
    Column,
    DataSchema,
    ForeignKey,
    Table,
    TableFingerprint,
    TableKind,
    TableRef,
)

SYSTEM_SCHEMAS = ("information_schema", "mysql", "performance_schema", "sys")
SAMPLE_MAX_LENGTH = 64
SAMPLE_MAX_VALUES = 25

TABLE_KINDS = {"BASE TABLE": TableKind.TABLE, "VIEW": TableKind.VIEW}

Row = dict[str, Any]

_ENUM = re.compile(r"^(?:enum|set)\((.*)\)$", re.IGNORECASE | re.DOTALL)
_ENUM_VALUE = re.compile(r"'((?:[^']|'')*)'")


def scope(
    schemas: Sequence[str], tables: Sequence[TableRef] | None, prefix: str = ""
) -> tuple[str, list[str]]:
    """A WHERE clause (and its parameters) limiting a query to the wanted tables."""
    schema_column, table_column = f"{prefix}TABLE_SCHEMA", f"{prefix}TABLE_NAME"
    if schemas:
        marks = ", ".join(["%s"] * len(schemas))
        clause, params = f"{schema_column} IN ({marks})", list(schemas)
    else:
        marks = ", ".join(["%s"] * len(SYSTEM_SCHEMAS))
        clause = f"{schema_column} NOT IN ({marks})"
        params = list(SYSTEM_SCHEMAS)
    if tables is not None:
        pairs = ", ".join(["(%s, %s)"] * len(tables))
        clause += f" AND ({schema_column}, {table_column}) IN ({pairs})"
        params += [part for t in tables for part in (t.schema_name, t.name)]
    return clause, params


def tables_sql(where: str) -> str:
    return f"""
SELECT TABLE_SCHEMA AS schema_name,
       TABLE_NAME AS table_name,
       TABLE_TYPE AS table_type,
       TABLE_ROWS AS row_estimate,
       TABLE_COMMENT AS table_comment
FROM information_schema.TABLES
WHERE TABLE_TYPE IN ('BASE TABLE', 'VIEW') AND {where}
ORDER BY TABLE_SCHEMA, TABLE_NAME
"""


def columns_sql(where: str) -> str:
    return f"""
SELECT TABLE_SCHEMA AS schema_name,
       TABLE_NAME AS table_name,
       COLUMN_NAME AS column_name,
       COLUMN_TYPE AS column_type,
       IS_NULLABLE AS is_nullable,
       COLUMN_COMMENT AS column_comment
FROM information_schema.COLUMNS
WHERE {where}
ORDER BY TABLE_SCHEMA, TABLE_NAME, ORDINAL_POSITION
"""


def constraints_sql(where: str) -> str:
    return f"""
SELECT TABLE_SCHEMA AS schema_name,
       TABLE_NAME AS table_name,
       CASE WHEN CONSTRAINT_NAME = 'PRIMARY'
            THEN 'PRIMARY KEY' ELSE 'FOREIGN KEY' END AS kind,
       COLUMN_NAME AS column_name,
       REFERENCED_TABLE_SCHEMA AS ref_schema,
       REFERENCED_TABLE_NAME AS ref_table,
       REFERENCED_COLUMN_NAME AS ref_column
FROM information_schema.KEY_COLUMN_USAGE
WHERE (CONSTRAINT_NAME = 'PRIMARY' OR REFERENCED_TABLE_NAME IS NOT NULL)
  AND {where}
ORDER BY TABLE_SCHEMA, TABLE_NAME, CONSTRAINT_NAME, ORDINAL_POSITION
"""


RELATION_INFO_SQL = """
SELECT TABLE_TYPE AS table_type, TABLE_ROWS AS row_estimate
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
"""


def enum_values(column_type: str) -> list[str]:
    """The allowed values of an ENUM or SET column, which are a free list of examples."""
    match = _ENUM.match(column_type.strip())
    if not match:
        return []
    values = [v.replace("''", "'") for v in _ENUM_VALUE.findall(match.group(1))]
    if len(values) > SAMPLE_MAX_VALUES:
        return []
    return [v for v in values if len(v) <= SAMPLE_MAX_LENGTH]


def build_fingerprints(
    tables: Iterable[Row], columns: Iterable[Row], constraints: Iterable[Row]
) -> list[TableFingerprint]:
    column_text: dict[tuple[str, str], list[str]] = {}
    for row in columns:
        key = (row["schema_name"], row["table_name"])
        column_text.setdefault(key, []).append(
            ":".join(
                [
                    row["column_name"],
                    row["column_type"],
                    row["is_nullable"],
                    row["column_comment"] or "",
                ]
            )
        )
    constraint_text: dict[tuple[str, str], list[str]] = {}
    for row in constraints:
        key = (row["schema_name"], row["table_name"])
        constraint_text.setdefault(key, []).append(
            ":".join(
                str(row[name])
                for name in (
                    "kind",
                    "column_name",
                    "ref_schema",
                    "ref_table",
                    "ref_column",
                )
            )
        )

    fingerprints = []
    for row in tables:
        key = (row["schema_name"], row["table_name"])
        digest = hashlib.md5(
            "|".join(
                [
                    _comment(row) or "",
                    ",".join(column_text.get(key, [])),
                    ",".join(sorted(constraint_text.get(key, []))),
                ]
            ).encode()
        ).hexdigest()
        fingerprints.append(
            TableFingerprint(
                table=TableRef(schema_name=key[0], name=key[1]),
                kind=TABLE_KINDS[row["table_type"]],
                fingerprint=digest,
                row_estimate=_estimate(row),
            )
        )
    return fingerprints


def build_schema(
    tables: Iterable[Row], columns: Iterable[Row], constraints: Iterable[Row]
) -> DataSchema:
    found: dict[tuple[str, str], Table] = {}
    for row in tables:
        found[(row["schema_name"], row["table_name"])] = Table(
            schema_name=row["schema_name"],
            name=row["table_name"],
            kind=TABLE_KINDS[row["table_type"]],
            row_estimate=_estimate(row),
            description=_comment(row),
        )

    for row in columns:
        table = found.get((row["schema_name"], row["table_name"]))
        if table is None:
            continue
        table.columns.append(
            Column(
                name=row["column_name"],
                data_type=row["column_type"],
                nullable=row["is_nullable"] == "YES",
                description=row["column_comment"] or None,
                sample_values=enum_values(row["column_type"]),
            )
        )

    for row in constraints:
        table = found.get((row["schema_name"], row["table_name"]))
        if table is None:
            continue
        if row["kind"] == "PRIMARY KEY":
            table.primary_key.append(row["column_name"])
        else:
            table.foreign_keys.append(
                ForeignKey(
                    column=row["column_name"],
                    references_table=f"{row['ref_schema']}.{row['ref_table']}",
                    references_column=row["ref_column"],
                )
            )
    return DataSchema(tables=list(found.values()))


def _comment(row: Row) -> str | None:
    # MySQL fills the comment of a view with the word VIEW.
    comment = row["table_comment"]
    return None if not comment or row["table_type"] == "VIEW" else comment


def _estimate(row: Row) -> int | None:
    return None if row["row_estimate"] is None else int(row["row_estimate"])
