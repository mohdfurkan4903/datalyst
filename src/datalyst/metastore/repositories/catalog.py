from collections.abc import Iterable, Mapping
from datetime import timedelta

import psycopg
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from datalyst.core.exceptions import NotFoundError
from datalyst.core.models import (
    Column,
    ColumnProfile,
    ColumnRef,
    DataSchema,
    ForeignKey,
    InferredRelationship,
    RelationshipOrigin,
    SyncSummary,
    Table,
    TableDescription,
    TableRef,
    ValueCount,
)
from datalyst.core.schema_diff import diff_columns

JOIN_CONFIDENCE = 0.8

_UPSERT_TABLE = """
INSERT INTO catalog_tables (
    source_id, schema_name, name, kind, row_estimate, comment, fingerprint, synced_at
) VALUES (%s, %s, %s, %s, %s, %s, %s, now())
ON CONFLICT (source_id, schema_name, name) DO UPDATE
SET kind = excluded.kind,
    fingerprint = excluded.fingerprint,
    row_estimate = excluded.row_estimate,
    comment = excluded.comment,
    synced_at = now(),
    removed_at = NULL
"""

_UPSERT_COLUMN = """
INSERT INTO catalog_columns (
    table_id, ordinal, name, data_type, nullable, is_primary_key, comment, sample_values
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (table_id, name) DO UPDATE
SET ordinal = excluded.ordinal,
    data_type = excluded.data_type,
    nullable = excluded.nullable,
    is_primary_key = excluded.is_primary_key,
    comment = excluded.comment,
    sample_values = excluded.sample_values
"""

_DELETE_MISSING_COLUMNS = """
DELETE FROM catalog_columns c
WHERE c.table_id = ANY(%(table_ids)s)
  AND NOT EXISTS (
      SELECT 1 FROM unnest(%(keep_tables)s::bigint[], %(keep_names)s::text[])
          AS keep(table_id, name)
      WHERE keep.table_id = c.table_id AND keep.name = c.name
  )
"""

_SOURCE_COLUMNS = """
SELECT c.id, t.schema_name, t.name AS table_name, c.name
FROM catalog_columns c
JOIN catalog_tables t ON t.id = c.table_id
WHERE t.source_id = %s AND t.removed_at IS NULL
"""

_REF_FILTER = """
(%(names)s::text[] IS NULL
 OR (t.schema_name, t.name) IN (SELECT * FROM unnest(%(schemas)s::text[], %(names)s::text[])))
"""

_EFFECTIVE_TABLE_DESCRIPTION = """
CASE WHEN t.description_origin = 'human' THEN t.description
     WHEN %(ai)s THEN coalesce(t.comment, t.description)
     ELSE t.comment END
"""

_EFFECTIVE_COLUMN_DESCRIPTION = """
CASE WHEN c.description_origin = 'human' THEN c.description
     WHEN %(ai)s THEN coalesce(c.comment, c.description)
     ELSE c.comment END
"""

_LOAD_TABLES = f"""
SELECT t.id, t.schema_name, t.name, t.kind, t.row_estimate,
       {_EFFECTIVE_TABLE_DESCRIPTION} AS description,
       CASE WHEN %(ai)s THEN t.synonyms ELSE '{{}}' END AS synonyms,
       CASE WHEN %(ai)s THEN t.example_questions ELSE '{{}}' END AS example_questions
FROM catalog_tables t
WHERE t.source_id = %(source)s AND t.removed_at IS NULL AND {_REF_FILTER}
ORDER BY t.schema_name, t.name
"""

_LOAD_COLUMNS = f"""
SELECT c.table_id, c.name, c.data_type, c.nullable, c.is_primary_key,
       {_EFFECTIVE_COLUMN_DESCRIPTION} AS description, c.sample_values, c.profile,
       CASE WHEN %(ai)s THEN c.synonyms ELSE '{{}}' END AS synonyms
FROM catalog_columns c
JOIN catalog_tables t ON t.id = c.table_id
WHERE t.source_id = %(source)s AND t.removed_at IS NULL AND {_REF_FILTER}
ORDER BY c.table_id, c.ordinal
"""

_LOAD_RELATIONSHIPS = f"""
SELECT fc.table_id, fc.name AS column_name,
       tt.schema_name || '.' || tt.name AS references_table,
       tc.name AS references_column
FROM relationships r
JOIN catalog_columns fc ON fc.id = r.from_column_id
JOIN catalog_tables t ON t.id = fc.table_id
JOIN catalog_columns tc ON tc.id = r.to_column_id
JOIN catalog_tables tt ON tt.id = tc.table_id
WHERE r.source_id = %(source)s
  AND r.status = 'active'
  AND r.confidence >= %(min_confidence)s
  AND (%(origins)s::text[] IS NULL OR r.origin = ANY(%(origins)s::text[]))
  AND tt.removed_at IS NULL
  AND {_REF_FILTER}
ORDER BY fc.table_id, fc.ordinal
"""

_LIST_RELATIONSHIPS = """
SELECT r.id, ft.schema_name || '.' || ft.name || '.' || fc.name AS child,
       tt.schema_name || '.' || tt.name || '.' || tc.name AS parent,
       r.origin, r.confidence, r.status, r.evidence
FROM relationships r
JOIN catalog_columns fc ON fc.id = r.from_column_id
JOIN catalog_tables ft ON ft.id = fc.table_id
JOIN catalog_columns tc ON tc.id = r.to_column_id
JOIN catalog_tables tt ON tt.id = tc.table_id
WHERE r.source_id = %s AND ft.removed_at IS NULL AND tt.removed_at IS NULL
ORDER BY child, parent
"""

TableKey = tuple[str, str]


class CatalogRepository:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def version(self, source_id: int) -> int:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT catalog_version FROM data_sources WHERE id = %s", (source_id,)
            ).fetchone()
        return row["catalog_version"] if row else 0

    def fingerprints(self, source_id: int) -> dict[TableRef, str | None]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """
                SELECT schema_name, name, fingerprint FROM catalog_tables
                WHERE source_id = %s AND removed_at IS NULL
                """,
                (source_id,),
            ).fetchall()
        return {
            TableRef(schema_name=row["schema_name"], name=row["name"]): row[
                "fingerprint"
            ]
            for row in rows
        }

    def apply_tables(
        self,
        source_id: int,
        tables: list[Table],
        fingerprints: Mapping[TableRef, str] | None = None,
    ) -> SyncSummary:
        fingerprints = fingerprints or {}
        summary = SyncSummary(tables=len(tables))
        if not tables:
            return summary
        with self._pool.connection() as conn, conn.transaction():
            before = self._snapshot(conn, source_id, [t.ref for t in tables])
            self.upsert_tables(conn, source_id, tables, fingerprints)
            for table in tables:
                previous = before.get(table.ref)
                if previous is None or previous["removed"]:
                    summary.added.append(table.qualified_name)
                    self._record_change(conn, source_id, table.ref, "added", {})
                    continue
                changes = diff_columns(previous["columns"], table)
                fingerprint_moved = previous["fingerprint"] != fingerprints.get(
                    table.ref
                )
                if not changes and not fingerprint_moved:
                    continue
                summary.altered.append(table.qualified_name)
                self._record_change(conn, source_id, table.ref, "altered", changes)
                self._mark_stale(conn, previous["id"], changes)
            if summary.changed:
                self._bump_version(conn, source_id)
        return summary

    def remove_tables(self, source_id: int, refs: Iterable[TableRef]) -> list[str]:
        refs = list(refs)
        if not refs:
            return []
        with self._pool.connection() as conn, conn.transaction():
            rows = conn.execute(
                """
                UPDATE catalog_tables t SET removed_at = now()
                FROM unnest(%s::text[], %s::text[]) AS k(schema_name, name)
                WHERE t.source_id = %s AND t.removed_at IS NULL
                  AND t.schema_name = k.schema_name AND t.name = k.name
                RETURNING t.schema_name, t.name
                """,
                ([r.schema_name for r in refs], [r.name for r in refs], source_id),
            ).fetchall()
            removed = [
                TableRef(schema_name=r["schema_name"], name=r["name"]) for r in rows
            ]
            for ref in removed:
                self._record_change(conn, source_id, ref, "removed", {})
            if removed:
                self._bump_version(conn, source_id)
        return sorted(ref.qualified_name for ref in removed)

    def update_row_estimates(
        self, source_id: int, estimates: Mapping[TableRef, int | None]
    ) -> None:
        if not estimates:
            return
        refs = list(estimates)
        with self._pool.connection() as conn:
            conn.execute(
                """
                UPDATE catalog_tables t SET row_estimate = k.row_estimate
                FROM unnest(%s::text[], %s::text[], %s::bigint[])
                    AS k(schema_name, name, row_estimate)
                WHERE t.source_id = %s
                  AND t.schema_name = k.schema_name AND t.name = k.name
                  AND t.row_estimate IS DISTINCT FROM k.row_estimate
                """,
                (
                    [r.schema_name for r in refs],
                    [r.name for r in refs],
                    [estimates[r] for r in refs],
                    source_id,
                ),
            )

    def save_profiles(
        self,
        source_id: int,
        table: TableRef,
        profiles: Mapping[str, ColumnProfile],
        values: Mapping[str, list[ValueCount]],
        sample_values: Mapping[str, list[str]],
    ) -> bool:
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """
                SELECT id FROM catalog_tables
                WHERE source_id = %s AND schema_name = %s AND name = %s
                  AND removed_at IS NULL
                """,
                (source_id, table.schema_name, table.name),
            ).fetchone()
            if row is None:
                return False
            table_id = row["id"]
            columns = {
                r["name"]: r["id"]
                for r in conn.execute(
                    "SELECT id, name FROM catalog_columns WHERE table_id = %s",
                    (table_id,),
                )
            }
            with conn.cursor() as cur:
                cur.executemany(
                    """
                    UPDATE catalog_columns
                    SET profile = %s, semantic_type = %s, sample_values = %s
                    WHERE id = %s
                    """,
                    [
                        (
                            Jsonb(
                                profile.model_dump(mode="json", exclude_defaults=True)
                            ),
                            profile.semantic_type.value
                            if profile.semantic_type
                            else None,
                            sample_values.get(name, []),
                            columns[name],
                        )
                        for name, profile in profiles.items()
                        if name in columns
                    ],
                )
                cur.execute(
                    "DELETE FROM column_values WHERE column_id = ANY(%s)",
                    (list(columns.values()),),
                )
                rows = [
                    (columns[name], item.value, item.count)
                    for name, items in values.items()
                    if name in columns
                    for item in items
                ]
                if rows:
                    cur.executemany(
                        """
                        INSERT INTO column_values (column_id, value, frequency)
                        VALUES (%s, %s, %s) ON CONFLICT DO NOTHING
                        """,
                        rows,
                    )
            conn.execute(
                "UPDATE catalog_tables SET profiled_at = now() WHERE id = %s",
                (table_id,),
            )
            self._bump_version(conn, source_id)
        return True

    def tables_to_profile(
        self, source_id: int, older_than: timedelta, limit: int = 500
    ) -> list[TableRef]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """
                SELECT schema_name, name FROM catalog_tables
                WHERE source_id = %s AND removed_at IS NULL
                  AND (profiled_at IS NULL OR profiled_at < now() - %s)
                ORDER BY profiled_at NULLS FIRST, id
                LIMIT %s
                """,
                (source_id, older_than, limit),
            ).fetchall()
        return [TableRef(schema_name=r["schema_name"], name=r["name"]) for r in rows]

    def replace_inferred_relationships(
        self, source_id: int, relationships: list[InferredRelationship]
    ) -> int:
        with self._pool.connection() as conn, conn.transaction():
            column_ids = {
                (row["schema_name"], row["table_name"], row["name"]): row["id"]
                for row in conn.execute(_SOURCE_COLUMNS, (source_id,))
            }

            def resolve(ref: ColumnRef) -> int | None:
                return column_ids.get(
                    (ref.table.schema_name, ref.table.name, ref.column)
                )

            rows = []
            for relationship in relationships:
                child, parent = (
                    resolve(relationship.child),
                    resolve(relationship.parent),
                )
                if child and parent:
                    rows.append(
                        (
                            source_id,
                            child,
                            parent,
                            relationship.confidence,
                            Jsonb(relationship.evidence),
                        )
                    )
            removed = conn.execute(
                """
                DELETE FROM relationships
                WHERE source_id = %s AND origin = 'inferred' AND status = 'active'
                  AND (from_column_id, to_column_id) NOT IN (
                      SELECT * FROM unnest(%s::bigint[], %s::bigint[])
                  )
                """,
                (source_id, [r[1] for r in rows], [r[2] for r in rows]),
            ).rowcount
            changed = removed
            with conn.cursor() as cur:
                for row in rows:
                    cur.execute(
                        """
                        INSERT INTO relationships (
                            source_id, from_column_id, to_column_id, origin,
                            confidence, evidence
                        ) VALUES (%s, %s, %s, 'inferred', %s, %s)
                        ON CONFLICT (from_column_id, to_column_id) DO UPDATE
                        SET confidence = excluded.confidence,
                            evidence = excluded.evidence,
                            updated_at = now()
                        WHERE relationships.origin = 'inferred'
                          AND relationships.status = 'active'
                          AND (relationships.confidence, relationships.evidence)
                              IS DISTINCT FROM (excluded.confidence, excluded.evidence)
                        """,
                        row,
                    )
                    changed += cur.rowcount
            if changed:
                self._bump_version(conn, source_id)
        return changed

    def incoming_joins(self, source_id: int, table: TableRef) -> list[str]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """
                SELECT ft.schema_name || '.' || ft.name || '.' || fc.name AS child
                FROM relationships r
                JOIN catalog_columns fc ON fc.id = r.from_column_id
                JOIN catalog_tables ft ON ft.id = fc.table_id
                JOIN catalog_columns tc ON tc.id = r.to_column_id
                JOIN catalog_tables tt ON tt.id = tc.table_id
                WHERE r.source_id = %s AND r.status = 'active' AND r.confidence >= %s
                  AND tt.schema_name = %s AND tt.name = %s AND ft.removed_at IS NULL
                ORDER BY child
                """,
                (source_id, JOIN_CONFIDENCE, table.schema_name, table.name),
            ).fetchall()
        return [row["child"] for row in rows]

    def description_state(self, source_id: int) -> dict[TableRef, dict]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """
                SELECT t.schema_name, t.name, t.description_hash,
                       t.description IS NULL
                       OR (t.description_status = 'stale'
                           AND t.description_origin IS DISTINCT FROM 'human')
                       OR EXISTS (
                           SELECT 1 FROM catalog_columns c
                           WHERE c.table_id = t.id AND c.description_status = 'stale'
                             AND c.description_origin IS DISTINCT FROM 'human'
                       ) AS incomplete
                FROM catalog_tables t
                WHERE t.source_id = %s AND t.removed_at IS NULL
                """,
                (source_id,),
            ).fetchall()
        return {TableRef(schema_name=r["schema_name"], name=r["name"]): r for r in rows}

    def save_description(
        self,
        source_id: int,
        table: TableRef,
        description: TableDescription,
        key: str,
    ) -> None:
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """
                UPDATE catalog_tables
                SET description = CASE WHEN description_origin = 'human'
                                       THEN description ELSE %(description)s END,
                    description_status = CASE WHEN description_origin = 'human'
                                              THEN description_status ELSE 'auto' END,
                    description_origin = coalesce(description_origin, 'ai'),
                    synonyms = %(synonyms)s,
                    example_questions = %(questions)s,
                    description_hash = %(key)s,
                    described_at = now()
                WHERE source_id = %(source)s AND schema_name = %(schema)s
                  AND name = %(name)s AND removed_at IS NULL
                RETURNING id
                """,
                {
                    "description": description.description,
                    "synonyms": description.synonyms,
                    "questions": description.example_questions,
                    "key": key,
                    "source": source_id,
                    "schema": table.schema_name,
                    "name": table.name,
                },
            ).fetchone()
            if row is None:
                return
            with conn.cursor() as cur:
                cur.executemany(
                    """
                    UPDATE catalog_columns
                    SET description = %s, description_origin = 'ai',
                        description_status = 'auto', synonyms = %s
                    WHERE table_id = %s AND name = %s
                      AND description_origin IS DISTINCT FROM 'human'
                    """,
                    [
                        (column.description, column.synonyms, row["id"], name)
                        for name, column in description.columns.items()
                    ],
                )
            self._bump_version(conn, source_id)

    def clear_description_keys(self, source_id: int) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "UPDATE catalog_tables SET description_hash = NULL WHERE source_id = %s",
                (source_id,),
            )

    def object_ids(self, source_id: int) -> dict[tuple[str, str, str | None], int]:
        with self._pool.connection() as conn:
            tables = conn.execute(
                """
                SELECT id, schema_name, name FROM catalog_tables
                WHERE source_id = %s AND removed_at IS NULL
                """,
                (source_id,),
            ).fetchall()
            columns = conn.execute(_SOURCE_COLUMNS, (source_id,)).fetchall()
        ids: dict[tuple[str, str, str | None], int] = {
            (r["schema_name"], r["name"], None): r["id"] for r in tables
        }
        ids.update(
            {(r["schema_name"], r["table_name"], r["name"]): r["id"] for r in columns}
        )
        return ids

    def set_description(
        self,
        source_id: int,
        table: TableRef,
        description: str | None,
        column: str | None = None,
    ) -> None:
        origin, status = ("human", "approved") if description else (None, None)
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """
                SELECT id FROM catalog_tables
                WHERE source_id = %s AND schema_name = %s AND name = %s
                  AND removed_at IS NULL
                """,
                (source_id, table.schema_name, table.name),
            ).fetchone()
            if row is None:
                raise NotFoundError(
                    f"Table {table.qualified_name} is not in the catalog."
                )
            if column is None:
                conn.execute(
                    """
                    UPDATE catalog_tables
                    SET description = %s, description_origin = %s,
                        description_status = %s, description_hash = NULL
                    WHERE id = %s
                    """,
                    (description, origin, status, row["id"]),
                )
            elif not conn.execute(
                """
                UPDATE catalog_columns
                SET description = %s, description_origin = %s, description_status = %s
                WHERE table_id = %s AND name = %s
                """,
                (description, origin, status, row["id"], column),
            ).rowcount:
                raise NotFoundError(
                    f"Column {table.qualified_name}.{column} does not exist."
                )
            self._bump_version(conn, source_id)

    def curation(self, source_id: int, table: TableRef) -> dict:
        with self._pool.connection() as conn:
            row = conn.execute(
                """
                SELECT id, schema_name, name, kind, row_estimate, comment, description,
                       description_origin, description_status, synonyms,
                       example_questions, synced_at, profiled_at, described_at
                FROM catalog_tables
                WHERE source_id = %s AND schema_name = %s AND name = %s
                  AND removed_at IS NULL
                """,
                (source_id, table.schema_name, table.name),
            ).fetchone()
            if row is None:
                raise NotFoundError(
                    f"Table {table.qualified_name} is not in the catalog."
                )
            row["columns"] = conn.execute(
                """
                SELECT name, data_type, nullable, is_primary_key, comment, description,
                       description_origin, description_status, semantic_type,
                       synonyms, sample_values, profile
                FROM catalog_columns WHERE table_id = %s ORDER BY ordinal
                """,
                (row.pop("id"),),
            ).fetchall()
        return row

    def stale_descriptions(self, source_id: int) -> list[dict]:
        with self._pool.connection() as conn:
            return conn.execute(
                """
                SELECT t.schema_name || '.' || t.name AS table_name, NULL AS column_name,
                       t.description, t.description_origin
                FROM catalog_tables t
                WHERE t.source_id = %(s)s AND t.removed_at IS NULL
                  AND t.description_status = 'stale'
                UNION ALL
                SELECT t.schema_name || '.' || t.name, c.name, c.description,
                       c.description_origin
                FROM catalog_columns c JOIN catalog_tables t ON t.id = c.table_id
                WHERE t.source_id = %(s)s AND t.removed_at IS NULL
                  AND c.description_status = 'stale'
                ORDER BY 1, 2 NULLS FIRST
                """,
                {"s": source_id},
            ).fetchall()

    def set_relationship_status(
        self, source_id: int, relationship_id: int, status: str
    ) -> None:
        with self._pool.connection() as conn, conn.transaction():
            if not conn.execute(
                """
                UPDATE relationships SET status = %s, updated_at = now()
                WHERE id = %s AND source_id = %s
                """,
                (status, relationship_id, source_id),
            ).rowcount:
                raise NotFoundError(f"Relationship {relationship_id} does not exist.")
            self._bump_version(conn, source_id)

    def add_relationship(
        self, source_id: int, child: ColumnRef, parent: ColumnRef
    ) -> None:
        with self._pool.connection() as conn, conn.transaction():
            ids = {
                (r["schema_name"], r["table_name"], r["name"]): r["id"]
                for r in conn.execute(_SOURCE_COLUMNS, (source_id,))
            }
            child_id = ids.get(
                (child.table.schema_name, child.table.name, child.column)
            )
            parent_id = ids.get(
                (parent.table.schema_name, parent.table.name, parent.column)
            )
            if not child_id or not parent_id:
                raise NotFoundError("Both columns must exist in the catalog.")
            conn.execute(
                """
                INSERT INTO relationships (
                    source_id, from_column_id, to_column_id, origin, confidence, status
                ) VALUES (%s, %s, %s, 'human', 1, 'active')
                ON CONFLICT (from_column_id, to_column_id) DO UPDATE
                SET origin = 'human', confidence = 1, status = 'active', updated_at = now()
                """,
                (source_id, child_id, parent_id),
            )
            self._bump_version(conn, source_id)

    def relationships(self, source_id: int) -> list[dict]:
        with self._pool.connection() as conn:
            return conn.execute(_LIST_RELATIONSHIPS, (source_id,)).fetchall()

    def recent_changes(self, source_id: int, limit: int = 20) -> list[dict]:
        with self._pool.connection() as conn:
            return conn.execute(
                """
                SELECT schema_name, table_name, change, details, detected_at
                FROM schema_changes WHERE source_id = %s
                ORDER BY id DESC LIMIT %s
                """,
                (source_id, limit),
            ).fetchall()

    def upsert_tables(
        self,
        conn: psycopg.Connection,
        source_id: int,
        tables: list[Table],
        fingerprints: Mapping[TableRef, str] | None = None,
    ) -> None:
        fingerprints = fingerprints or {}
        if not tables:
            return
        with conn.cursor() as cur:
            cur.executemany(
                _UPSERT_TABLE,
                [
                    (
                        source_id,
                        t.schema_name,
                        t.name,
                        t.kind.value,
                        t.row_estimate,
                        t.description,
                        fingerprints.get(t.ref),
                    )
                    for t in tables
                ],
            )
        table_ids = self._table_ids(
            conn, source_id, [(t.schema_name, t.name) for t in tables]
        )

        column_rows, keep_tables, keep_names = [], [], []
        for table in tables:
            table_id = table_ids[(table.schema_name, table.name)]
            for ordinal, column in enumerate(table.columns, start=1):
                column_rows.append(
                    (
                        table_id,
                        ordinal,
                        column.name,
                        column.data_type,
                        column.nullable,
                        column.name in table.primary_key,
                        column.description,
                        column.sample_values,
                    )
                )
                keep_tables.append(table_id)
                keep_names.append(column.name)
        with conn.cursor() as cur:
            if column_rows:
                cur.executemany(_UPSERT_COLUMN, column_rows)
            cur.execute(
                _DELETE_MISSING_COLUMNS,
                {
                    "table_ids": list(table_ids.values()),
                    "keep_tables": keep_tables,
                    "keep_names": keep_names,
                },
            )
        self._replace_declared_relationships(conn, source_id, tables, table_ids)

    def load_schema(
        self,
        source_id: int,
        min_confidence: float = JOIN_CONFIDENCE,
        *,
        tables: list[TableRef] | None = None,
        origins: list[RelationshipOrigin] | None = None,
        ai_descriptions: bool = True,
    ) -> DataSchema:
        params = {
            "source": source_id,
            "ai": ai_descriptions,
            "min_confidence": min_confidence,
            "origins": [o.value for o in origins] if origins else None,
            "schemas": [r.schema_name for r in tables] if tables is not None else None,
            "names": [r.name for r in tables] if tables is not None else None,
        }
        with self._pool.connection() as conn:
            table_rows = conn.execute(_LOAD_TABLES, params).fetchall()
            column_rows = conn.execute(_LOAD_COLUMNS, params).fetchall()
            relationship_rows = conn.execute(_LOAD_RELATIONSHIPS, params).fetchall()

        tables = {
            row["id"]: Table(
                schema_name=row["schema_name"],
                name=row["name"],
                kind=row["kind"],
                row_estimate=row["row_estimate"],
                description=row["description"],
                synonyms=row["synonyms"],
                example_questions=row["example_questions"],
            )
            for row in table_rows
        }
        for row in column_rows:
            table = tables[row["table_id"]]
            table.columns.append(
                Column(
                    name=row["name"],
                    data_type=row["data_type"],
                    nullable=row["nullable"],
                    description=row["description"],
                    sample_values=row["sample_values"],
                    synonyms=row["synonyms"],
                    profile=ColumnProfile(**row["profile"]) if row["profile"] else None,
                )
            )
            if row["is_primary_key"]:
                table.primary_key.append(row["name"])
        for row in relationship_rows:
            table = tables.get(row["table_id"])
            if table is not None:
                table.foreign_keys.append(
                    ForeignKey(
                        column=row["column_name"],
                        references_table=row["references_table"],
                        references_column=row["references_column"],
                    )
                )
        return DataSchema(tables=list(tables.values()))

    def _snapshot(
        self, conn: psycopg.Connection, source_id: int, refs: list[TableRef]
    ) -> dict[TableRef, dict]:
        rows = conn.execute(
            """
            SELECT t.id, t.schema_name, t.name, t.fingerprint,
                   t.removed_at IS NOT NULL AS removed,
                   coalesce(
                       jsonb_object_agg(c.name, c.data_type) FILTER (WHERE c.id IS NOT NULL),
                       '{}'
                   ) AS columns
            FROM catalog_tables t
            JOIN unnest(%s::text[], %s::text[]) AS k(schema_name, name)
              ON k.schema_name = t.schema_name AND k.name = t.name
            LEFT JOIN catalog_columns c ON c.table_id = t.id
            WHERE t.source_id = %s
            GROUP BY t.id
            """,
            ([r.schema_name for r in refs], [r.name for r in refs], source_id),
        ).fetchall()
        return {
            TableRef(schema_name=row["schema_name"], name=row["name"]): row
            for row in rows
        }

    def _table_ids(
        self, conn: psycopg.Connection, source_id: int, keys: list[TableKey]
    ) -> dict[TableKey, int]:
        rows = conn.execute(
            """
            SELECT t.id, t.schema_name, t.name
            FROM catalog_tables t
            JOIN unnest(%s::text[], %s::text[]) AS k(schema_name, name)
              ON k.schema_name = t.schema_name AND k.name = t.name
            WHERE t.source_id = %s
            """,
            ([s for s, _ in keys], [n for _, n in keys], source_id),
        ).fetchall()
        return {(row["schema_name"], row["name"]): row["id"] for row in rows}

    def _replace_declared_relationships(
        self,
        conn: psycopg.Connection,
        source_id: int,
        tables: list[Table],
        table_ids: dict[TableKey, int],
    ) -> None:
        conn.execute(
            """
            DELETE FROM relationships r
            USING catalog_columns c
            WHERE r.from_column_id = c.id
              AND r.origin = 'declared'
              AND c.table_id = ANY(%s)
            """,
            (list(table_ids.values()),),
        )
        column_ids = {
            (row["schema_name"], row["table_name"], row["name"]): row["id"]
            for row in conn.execute(_SOURCE_COLUMNS, (source_id,))
        }
        rows = []
        for table in tables:
            for fk in table.foreign_keys:
                ref_schema, _, ref_table = fk.references_table.partition(".")
                from_id = column_ids.get((table.schema_name, table.name, fk.column))
                to_id = column_ids.get((ref_schema, ref_table, fk.references_column))
                if from_id and to_id:
                    rows.append((source_id, from_id, to_id))
        if rows:
            with conn.cursor() as cur:
                cur.executemany(
                    """
                    INSERT INTO relationships (source_id, from_column_id, to_column_id, origin)
                    VALUES (%s, %s, %s, 'declared')
                    ON CONFLICT (from_column_id, to_column_id) DO UPDATE
                    SET origin = 'declared', confidence = 1, status = 'active',
                        updated_at = now()
                    """,
                    rows,
                )

    def _record_change(
        self,
        conn: psycopg.Connection,
        source_id: int,
        ref: TableRef,
        change: str,
        details: dict,
    ) -> None:
        conn.execute(
            """
            INSERT INTO schema_changes (source_id, schema_name, table_name, change, details)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (source_id, ref.schema_name, ref.name, change, Jsonb(details)),
        )

    def _mark_stale(
        self, conn: psycopg.Connection, table_id: int, changes: dict
    ) -> None:
        if changes.get("added") or changes.get("removed"):
            conn.execute(
                """
                UPDATE catalog_tables SET description_status = 'stale'
                WHERE id = %s AND description IS NOT NULL
                """,
                (table_id,),
            )
        if retyped := list(changes.get("retyped", {})):
            conn.execute(
                """
                UPDATE catalog_columns SET description_status = 'stale'
                WHERE table_id = %s AND name = ANY(%s) AND description IS NOT NULL
                """,
                (table_id, retyped),
            )

    def _bump_version(self, conn: psycopg.Connection, source_id: int) -> None:
        conn.execute(
            """
            UPDATE data_sources
            SET catalog_version = catalog_version + 1, last_synced_at = now(),
                updated_at = now()
            WHERE id = %s
            """,
            (source_id,),
        )
