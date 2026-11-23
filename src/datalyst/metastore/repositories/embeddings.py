from collections.abc import Iterable

from psycopg_pool import ConnectionPool

ObjectKey = tuple[str, int]


class EmbeddingRepository:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def hashes(self, source_id: int, model: str) -> dict[ObjectKey, str]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """
                SELECT object_type, object_id, content_hash FROM embeddings
                WHERE source_id = %s AND model = %s
                """,
                (source_id, model),
            ).fetchall()
        return {(r["object_type"], r["object_id"]): r["content_hash"] for r in rows}

    def save(
        self,
        source_id: int,
        model: str,
        rows: Iterable[tuple[str, int, str, str]],
    ) -> int:
        rows = list(rows)
        if not rows:
            return 0
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO embeddings (
                    source_id, object_type, object_id, model, content_hash, embedding
                ) VALUES (%s, %s, %s, %s, %s, %s::vector)
                ON CONFLICT (source_id, object_type, object_id, model) DO UPDATE
                SET content_hash = excluded.content_hash,
                    embedding = excluded.embedding,
                    updated_at = now()
                """,
                [
                    (source_id, kind, oid, model, digest, vector)
                    for kind, oid, digest, vector in rows
                ],
            )
        return len(rows)

    def keep_only(self, source_id: int, model: str, keep: Iterable[ObjectKey]) -> int:
        keep = list(keep)
        with self._pool.connection() as conn:
            return conn.execute(
                """
                DELETE FROM embeddings
                WHERE source_id = %s
                  AND (model <> %s OR (object_type, object_id) NOT IN (
                      SELECT * FROM unnest(%s::text[], %s::bigint[])
                  ))
                """,
                (source_id, model, [k for k, _ in keep], [i for _, i in keep]),
            ).rowcount

    def search(
        self,
        source_id: int,
        model: str,
        vector: str,
        object_type: str,
        limit: int = 20,
    ) -> list[tuple[int, float]]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """
                SELECT object_id, 1 - (embedding <=> %(vector)s::vector) AS similarity
                FROM embeddings
                WHERE source_id = %(source)s AND model = %(model)s
                  AND object_type = %(kind)s
                ORDER BY embedding <=> %(vector)s::vector
                LIMIT %(limit)s
                """,
                {
                    "vector": vector,
                    "source": source_id,
                    "model": model,
                    "kind": object_type,
                    "limit": limit,
                },
            ).fetchall()
        return [(r["object_id"], float(r["similarity"])) for r in rows]

    def search_tables(
        self, source_id: int, model: str, vector: str, limit: int = 20
    ) -> list[tuple[str, float]]:
        return self._named_search(
            """
            SELECT t.schema_name || '.' || t.name AS name,
                   1 - (e.embedding <=> %(vector)s::vector) AS similarity
            FROM embeddings e
            JOIN catalog_tables t ON t.id = e.object_id AND t.removed_at IS NULL
            WHERE e.source_id = %(source)s AND e.model = %(model)s
              AND e.object_type = 'table'
            ORDER BY e.embedding <=> %(vector)s::vector
            LIMIT %(limit)s
            """,
            source_id,
            model,
            vector,
            limit,
        )

    def search_columns(
        self, source_id: int, model: str, vector: str, limit: int = 40
    ) -> list[tuple[str, float]]:
        return self._named_search(
            """
            SELECT t.schema_name || '.' || t.name || '.' || c.name AS name,
                   1 - (e.embedding <=> %(vector)s::vector) AS similarity
            FROM embeddings e
            JOIN catalog_columns c ON c.id = e.object_id
            JOIN catalog_tables t ON t.id = c.table_id AND t.removed_at IS NULL
            WHERE e.source_id = %(source)s AND e.model = %(model)s
              AND e.object_type = 'column'
            ORDER BY e.embedding <=> %(vector)s::vector
            LIMIT %(limit)s
            """,
            source_id,
            model,
            vector,
            limit,
        )

    def _named_search(
        self, sql: str, source_id: int, model: str, vector: str, limit: int
    ) -> list[tuple[str, float]]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                sql,
                {"vector": vector, "source": source_id, "model": model, "limit": limit},
            ).fetchall()
        return [(r["name"], float(r["similarity"])) for r in rows]
