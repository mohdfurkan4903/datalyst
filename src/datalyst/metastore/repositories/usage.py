from datetime import timedelta

from psycopg_pool import ConnectionPool

from datalyst.core.models import LLMUsage


class UsageRepository:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def record(self, source_id: int | None, usage: list[LLMUsage]) -> None:
        if not usage:
            return
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO llm_usage (
                    source_id, purpose, model, input_tokens, output_tokens, duration_ms
                ) VALUES (%s, %s, %s, %s, %s, %s)
                """,
                [
                    (
                        source_id,
                        u.purpose,
                        u.model,
                        u.input_tokens,
                        u.output_tokens,
                        u.duration_ms,
                    )
                    for u in usage
                ],
            )

    def summary(
        self, source_id: int | None = None, since: timedelta = timedelta(days=30)
    ) -> list[dict]:
        with self._pool.connection() as conn:
            return conn.execute(
                """
                SELECT purpose, model, count(*) AS calls,
                       coalesce(sum(input_tokens), 0) AS input_tokens,
                       coalesce(sum(output_tokens), 0) AS output_tokens
                FROM llm_usage
                WHERE created_at > now() - %(since)s
                  AND (%(source)s::bigint IS NULL OR source_id = %(source)s)
                GROUP BY purpose, model ORDER BY purpose, model
                """,
                {"since": since, "source": source_id},
            ).fetchall()
