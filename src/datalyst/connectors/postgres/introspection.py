from collections.abc import Iterable

from datalyst.core.models import Column, DataSchema, ForeignKey, Table

COLUMNS_SQL = """
SELECT c.table_schema, c.table_name, c.column_name, c.data_type, c.is_nullable = 'YES'
FROM information_schema.columns c
JOIN information_schema.tables t
  ON t.table_schema = c.table_schema AND t.table_name = c.table_name
WHERE c.table_schema NOT IN ('pg_catalog', 'information_schema')
  AND c.table_schema NOT LIKE 'pg_toast%'
  AND t.table_type IN ('BASE TABLE', 'VIEW')
ORDER BY c.table_schema, c.table_name, c.ordinal_position
"""

CONSTRAINTS_SQL = """
SELECT n.nspname,
       c.relname,
       CASE con.contype WHEN 'p' THEN 'PRIMARY KEY' ELSE 'FOREIGN KEY' END,
       a.attname,
       ref_n.nspname,
       ref_c.relname,
       ref_a.attname
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
WHERE con.contype IN ('p', 'f')
  AND n.nspname NOT IN ('pg_catalog', 'information_schema')
  AND n.nspname NOT LIKE 'pg_toast%'
ORDER BY n.nspname, c.relname, con.conname, k.position
"""

ColumnRow = tuple[str, str, str, str, bool]
ConstraintRow = tuple[str, str, str, str, str | None, str | None, str | None]


def build_schema(
    column_rows: Iterable[ColumnRow],
    constraint_rows: Iterable[ConstraintRow],
    include_schemas: set[str] | None = None,
) -> DataSchema:
    tables: dict[tuple[str, str], Table] = {}

    for schema, table, column, data_type, nullable in column_rows:
        if include_schemas and schema not in include_schemas:
            continue
        key = (schema, table)
        if key not in tables:
            tables[key] = Table(schema_name=schema, name=table)
        tables[key].columns.append(
            Column(name=column, data_type=data_type, nullable=nullable)
        )

    for (
        schema,
        table,
        kind,
        column,
        ref_schema,
        ref_table,
        ref_column,
    ) in constraint_rows:
        target = tables.get((schema, table))
        if target is None:
            continue
        if kind == "PRIMARY KEY":
            target.primary_key.append(column)
        elif ref_table and ref_column:
            target.foreign_keys.append(
                ForeignKey(
                    column=column,
                    references_table=f"{ref_schema}.{ref_table}",
                    references_column=ref_column,
                )
            )

    return DataSchema(tables=list(tables.values()))
