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

SAMPLE_MAX_DISTINCT = 25
SAMPLE_MAX_LENGTH = 64
SAMPLE_CATEGORIES = frozenset({"S", "E"})

RELATION_KINDS = {
    "r": TableKind.TABLE,
    "p": TableKind.TABLE,
    "v": TableKind.VIEW,
    "m": TableKind.MATERIALIZED_VIEW,
}

_VISIBLE_RELATIONS = """
c.relkind IN ('r', 'p', 'v', 'm')
  AND NOT c.relispartition
  AND n.nspname NOT IN ('pg_catalog', 'information_schema')
  AND n.nspname NOT LIKE 'pg_toast%%'
  AND n.nspname NOT LIKE 'pg_temp%%'
  AND has_table_privilege(c.oid, 'SELECT')
  AND (cardinality(%(schemas)s::text[]) = 0 OR n.nspname = ANY(%(schemas)s::text[]))
  AND (
      %(ref_names)s::text[] IS NULL
      OR (n.nspname, c.relname) IN (
          SELECT * FROM unnest(%(ref_schemas)s::text[], %(ref_names)s::text[])
      )
  )
"""

_ROW_ESTIMATE = "CASE WHEN c.reltuples < 0 THEN NULL ELSE c.reltuples::bigint END"

FINGERPRINTS_SQL = f"""
SELECT n.nspname AS schema_name,
       c.relname AS name,
       c.relkind AS relkind,
       {_ROW_ESTIMATE} AS row_estimate,
       md5(concat_ws('|',
           obj_description(c.oid, 'pg_class'),
           (SELECT string_agg(
                       concat_ws(':', a.attname, format_type(a.atttypid, a.atttypmod),
                                 a.attnotnull, col_description(c.oid, a.attnum)),
                       ',' ORDER BY a.attnum)
              FROM pg_attribute a
             WHERE a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped),
           (SELECT string_agg(pg_get_constraintdef(con.oid), ','
                              ORDER BY pg_get_constraintdef(con.oid))
              FROM pg_constraint con
             WHERE con.conrelid = c.oid AND con.contype IN ('p', 'f'))
       )) AS fingerprint
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE {_VISIBLE_RELATIONS}
ORDER BY 1, 2
"""

COLUMNS_SQL = f"""
SELECT n.nspname AS schema_name,
       c.relname AS table_name,
       a.attname AS column_name,
       format_type(a.atttypid, a.atttypmod) AS data_type,
       NOT a.attnotnull AS nullable,
       obj_description(c.oid, 'pg_class') AS table_comment,
       col_description(c.oid, a.attnum) AS column_comment,
       c.relkind AS relkind,
       t.typcategory AS type_category,
       {_ROW_ESTIMATE} AS row_estimate
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
JOIN pg_type t ON t.oid = a.atttypid
WHERE {_VISIBLE_RELATIONS}
ORDER BY n.nspname, c.relname, a.attnum
"""

CONSTRAINTS_SQL = f"""
SELECT n.nspname AS schema_name,
       c.relname AS table_name,
       CASE con.contype WHEN 'p' THEN 'PRIMARY KEY' ELSE 'FOREIGN KEY' END AS kind,
       a.attname AS column_name,
       ref_n.nspname AS ref_schema,
       ref_c.relname AS ref_table,
       ref_a.attname AS ref_column
FROM pg_constraint con
JOIN pg_class c ON c.oid = con.conrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
CROSS JOIN LATERAL unnest(con.conkey, con.confkey)
     WITH ORDINALITY AS k(attnum, ref_attnum, position)
JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = k.attnum
LEFT JOIN pg_class ref_c ON ref_c.oid = con.confrelid
LEFT JOIN pg_namespace ref_n ON ref_n.oid = ref_c.relnamespace
LEFT JOIN pg_attribute ref_a
       ON ref_a.attrelid = con.confrelid AND ref_a.attnum = k.ref_attnum
WHERE con.contype IN ('p', 'f') AND {_VISIBLE_RELATIONS}
ORDER BY n.nspname, c.relname, con.conname, k.position
"""

SAMPLE_VALUES_SQL = """
SELECT s.schemaname AS schema_name,
       s.tablename AS table_name,
       s.attname AS column_name,
       s.most_common_vals::text::text[] AS common_values
FROM pg_stats s
WHERE s.most_common_vals IS NOT NULL
  AND s.n_distinct > 0
  AND s.n_distinct <= %(max_distinct)s
  AND (cardinality(%(schemas)s::text[]) = 0 OR s.schemaname = ANY(%(schemas)s::text[]))
  AND (
      %(ref_names)s::text[] IS NULL
      OR (s.schemaname, s.tablename) IN (
          SELECT * FROM unnest(%(ref_schemas)s::text[], %(ref_names)s::text[])
      )
  )
"""

Row = dict[str, Any]


def relation_filter(
    include_schemas: Iterable[str], tables: Sequence[TableRef] | None
) -> dict[str, list[str] | None]:
    return {
        "schemas": sorted(include_schemas),
        "ref_schemas": None if tables is None else [t.schema_name for t in tables],
        "ref_names": None if tables is None else [t.name for t in tables],
    }


def build_fingerprints(rows: Iterable[Row]) -> list[TableFingerprint]:
    return [
        TableFingerprint(
            table=TableRef(schema_name=row["schema_name"], name=row["name"]),
            kind=RELATION_KINDS[row["relkind"]],
            fingerprint=row["fingerprint"],
            row_estimate=row["row_estimate"],
        )
        for row in rows
    ]


def build_schema(
    column_rows: Iterable[Row],
    constraint_rows: Iterable[Row],
    sample_rows: Iterable[Row] = (),
) -> DataSchema:
    tables: dict[tuple[str, str], Table] = {}
    sampleable: set[tuple[str, str, str]] = set()

    for row in column_rows:
        key = (row["schema_name"], row["table_name"])
        if key not in tables:
            tables[key] = Table(
                schema_name=row["schema_name"],
                name=row["table_name"],
                kind=RELATION_KINDS[row["relkind"]],
                row_estimate=row["row_estimate"],
                description=row["table_comment"],
            )
        tables[key].columns.append(
            Column(
                name=row["column_name"],
                data_type=row["data_type"],
                nullable=row["nullable"],
                description=row["column_comment"],
            )
        )
        if row["type_category"] in SAMPLE_CATEGORIES:
            sampleable.add((*key, row["column_name"]))

    for row in constraint_rows:
        target = tables.get((row["schema_name"], row["table_name"]))
        if target is None:
            continue
        if row["kind"] == "PRIMARY KEY":
            target.primary_key.append(row["column_name"])
        elif row["ref_table"] and row["ref_column"]:
            target.foreign_keys.append(
                ForeignKey(
                    column=row["column_name"],
                    references_table=f"{row['ref_schema']}.{row['ref_table']}",
                    references_column=row["ref_column"],
                )
            )

    for row in sample_rows:
        key = (row["schema_name"], row["table_name"])
        target = tables.get(key)
        if target is None or (*key, row["column_name"]) not in sampleable:
            continue
        column = next(c for c in target.columns if c.name == row["column_name"])
        column.sample_values = [
            value for value in row["common_values"] if len(value) <= SAMPLE_MAX_LENGTH
        ]

    return DataSchema(tables=list(tables.values()))
