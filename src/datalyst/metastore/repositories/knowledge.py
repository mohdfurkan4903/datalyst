import psycopg
from psycopg_pool import ConnectionPool

from datalyst.core.exceptions import ConfigurationError, NotFoundError
from datalyst.core.models import GlossaryTerm, ValueHint, VerifiedQuery

MIN_VALUE_LENGTH = 3

_VALUE_MATCHES = """
SELECT q.term, m.table_name, m.column_name, m.value, m.score
FROM unnest(%(terms)s::text[]) AS q(term)
CROSS JOIN LATERAL (
    SELECT t.schema_name || '.' || t.name AS table_name, c.name AS column_name,
           v.value, similarity(v.value, q.term) AS score
    FROM column_values v
    JOIN catalog_columns c ON c.id = v.column_id
    JOIN catalog_tables t ON t.id = c.table_id
    WHERE t.source_id = %(source)s AND t.removed_at IS NULL
      AND length(v.value) >= %(min_length)s
      AND length(v.value) <= 2 * length(q.term) + 2
      AND v.value %% q.term
    ORDER BY similarity(v.value, q.term) DESC, v.frequency DESC
    LIMIT %(per_term)s
) AS m
WHERE m.score >= %(min_score)s
ORDER BY m.score DESC
"""


class KnowledgeRepository:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def save_term(self, source_id: int, term: GlossaryTerm) -> GlossaryTerm:
        with self._pool.connection() as conn:
            row = conn.execute(
                """
                INSERT INTO glossary_terms (source_id, term, definition, sql_hint, created_by)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (source_id, term) DO UPDATE
                SET definition = excluded.definition, sql_hint = excluded.sql_hint,
                    updated_at = now()
                RETURNING id, term, definition, sql_hint, created_by
                """,
                (
                    source_id,
                    term.term.strip(),
                    term.definition,
                    term.sql_hint,
                    term.created_by,
                ),
            ).fetchone()
        return GlossaryTerm(**row)

    def glossary(self, source_id: int) -> list[GlossaryTerm]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """
                SELECT id, term, definition, sql_hint, created_by FROM glossary_terms
                WHERE source_id = %s ORDER BY lower(term)
                """,
                (source_id,),
            ).fetchall()
        return [GlossaryTerm(**row) for row in rows]

    def delete_term(self, source_id: int, term: str) -> None:
        with self._pool.connection() as conn:
            deleted = conn.execute(
                "DELETE FROM glossary_terms WHERE source_id = %s AND lower(term) = lower(%s)",
                (source_id, term),
            ).rowcount
        if not deleted:
            raise NotFoundError(f"No glossary term '{term}'.")

    def save_example(self, source_id: int, example: VerifiedQuery) -> VerifiedQuery:
        try:
            with self._pool.connection() as conn:
                row = conn.execute(
                    """
                    INSERT INTO verified_queries (
                        source_id, question, sql, tables, status, created_by
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING id, question, sql, tables, status, created_by
                    """,
                    (
                        source_id,
                        example.question.strip(),
                        example.sql.strip(),
                        example.tables,
                        example.status,
                        example.created_by,
                    ),
                ).fetchone()
        except psycopg.errors.CheckViolation as e:
            raise ConfigurationError(f"Invalid example: {e}") from e
        return VerifiedQuery(**row)

    def examples(
        self, source_id: int, ids: list[int] | None = None, status: str = "approved"
    ) -> list[VerifiedQuery]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """
                SELECT id, question, sql, tables, status, created_by FROM verified_queries
                WHERE source_id = %(source)s AND status = %(status)s
                  AND (%(ids)s::bigint[] IS NULL OR id = ANY(%(ids)s))
                ORDER BY id
                """,
                {"source": source_id, "status": status, "ids": ids},
            ).fetchall()
        examples = [VerifiedQuery(**row) for row in rows]
        if ids is not None:
            order = {example_id: i for i, example_id in enumerate(ids)}
            examples.sort(key=lambda e: order.get(e.id, len(order)))
        return examples

    def delete_example(self, source_id: int, example_id: int) -> None:
        with self._pool.connection() as conn:
            deleted = conn.execute(
                "DELETE FROM verified_queries WHERE source_id = %s AND id = %s",
                (source_id, example_id),
            ).rowcount
        if not deleted:
            raise NotFoundError(f"No example {example_id}.")

    def value_matches(
        self,
        source_id: int,
        terms: list[str],
        *,
        min_score: float = 0.6,
        per_term: int = 3,
    ) -> list[ValueHint]:
        if not terms:
            return []
        with self._pool.connection() as conn:
            rows = conn.execute(
                _VALUE_MATCHES,
                {
                    "terms": terms,
                    "source": source_id,
                    "min_length": MIN_VALUE_LENGTH,
                    "per_term": per_term,
                    "min_score": min_score,
                },
            ).fetchall()
        return [
            ValueHint(
                term=row["term"],
                table=row["table_name"],
                column=row["column_name"],
                value=row["value"],
                score=round(float(row["score"]), 3),
            )
            for row in rows
        ]
