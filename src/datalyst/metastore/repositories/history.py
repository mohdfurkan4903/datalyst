from psycopg_pool import ConnectionPool

from datalyst.core.exceptions import NotFoundError
from datalyst.core.models import AnalysisResult, HistoryEntry, Turn

_COLUMNS = """
id, user_id, source_id, question, rewritten_question, tables, sql, status, error,
attempts, row_count, duration_ms, feedback, feedback_note, conversation_id,
created_at
"""


def status_of(result: AnalysisResult) -> str:
    if result.success:
        return "success"
    if result.error and result.error.startswith("Declined:"):
        return "declined"
    return "error"


class HistoryRepository:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def record(
        self,
        user_id: int | None,
        source_id: int,
        result: AnalysisResult,
        conversation_id: int | None = None,
        preview: str | None = None,
    ) -> int:
        with self._pool.connection() as conn:
            row = conn.execute(
                """
                INSERT INTO query_history (
                    user_id, source_id, question, rewritten_question, tables, sql,
                    status, error, attempts, row_count, duration_ms,
                    conversation_id, result_preview
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    user_id,
                    source_id,
                    result.question,
                    result.rewritten_question,
                    result.tables,
                    result.query,
                    status_of(result),
                    result.error,
                    result.attempts,
                    result.result.row_count if result.result else None,
                    round(result.duration_ms),
                    conversation_id,
                    preview,
                ),
            ).fetchone()
        return row["id"]

    def get(self, entry_id: int) -> HistoryEntry:
        with self._pool.connection() as conn:
            row = conn.execute(
                f"SELECT {_COLUMNS} FROM query_history WHERE id = %s", (entry_id,)
            ).fetchone()
        if row is None:
            raise NotFoundError(f"History entry {entry_id} does not exist.")
        return HistoryEntry(**row)

    def list(
        self,
        *,
        user_id: int | None = None,
        source_id: int | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[HistoryEntry]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                f"""
                SELECT {_COLUMNS} FROM query_history
                WHERE (%(user)s::bigint IS NULL OR user_id = %(user)s)
                  AND (%(source)s::bigint IS NULL OR source_id = %(source)s)
                ORDER BY id DESC LIMIT %(limit)s OFFSET %(offset)s
                """,
                {
                    "user": user_id,
                    "source": source_id,
                    "limit": limit,
                    "offset": offset,
                },
            ).fetchall()
        return [HistoryEntry(**row) for row in rows]

    def conversation(self, conversation_id: int) -> "list[HistoryEntry]":
        """Every exchange of a chat, oldest first."""
        with self._pool.connection() as conn:
            rows = conn.execute(
                f"SELECT {_COLUMNS} FROM query_history "
                "WHERE conversation_id = %s ORDER BY id",
                (conversation_id,),
            ).fetchall()
        return [HistoryEntry(**row) for row in rows]

    def turns(self, conversation_id: int, limit: int) -> "list[Turn]":
        """The latest exchanges of a chat that the agent can build on, oldest first."""
        if limit <= 0:
            return []
        with self._pool.connection() as conn:
            rows = conn.execute(
                """
                SELECT question, sql, status, error, row_count, result_preview
                FROM (
                    SELECT id, question, sql, status, error, row_count, result_preview
                    FROM query_history
                    WHERE conversation_id = %s AND status IN ('success', 'declined')
                    ORDER BY id DESC LIMIT %s
                ) AS latest
                ORDER BY id
                """,
                (conversation_id, limit),
            ).fetchall()
        return [
            Turn(
                question=row["question"],
                query=row["sql"],
                row_count=row["row_count"],
                preview=row["result_preview"],
                declined=row["error"] if row["status"] == "declined" else None,
            )
            for row in rows
        ]

    def set_feedback(self, entry_id: int, value: int | None, note: str | None) -> None:
        with self._pool.connection() as conn:
            updated = conn.execute(
                "UPDATE query_history SET feedback = %s, feedback_note = %s WHERE id = %s",
                (value, note, entry_id),
            ).rowcount
        if not updated:
            raise NotFoundError(f"History entry {entry_id} does not exist.")
