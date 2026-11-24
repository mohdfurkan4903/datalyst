from psycopg_pool import ConnectionPool

from datalyst.core.exceptions import NotFoundError
from datalyst.core.models import Conversation

TITLE_LENGTH = 80

_SELECT = """
SELECT c.id, c.user_id, c.source_id, c.title, c.created_at, c.updated_at,
       (SELECT count(*) FROM query_history h WHERE h.conversation_id = c.id) AS turns
FROM conversations c
"""


class ConversationRepository:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def create(self, user_id: int, source_id: int, first_question: str) -> Conversation:
        title = " ".join(first_question.split())
        if len(title) > TITLE_LENGTH:
            title = title[: TITLE_LENGTH - 1].rstrip() + "…"
        with self._pool.connection() as conn:
            row = conn.execute(
                "INSERT INTO conversations (user_id, source_id, title) "
                "VALUES (%s, %s, %s) RETURNING id",
                (user_id, source_id, title),
            ).fetchone()
        return self.get(row["id"])

    def get(self, conversation_id: int) -> Conversation:
        with self._pool.connection() as conn:
            row = conn.execute(
                f"{_SELECT} WHERE c.id = %s", (conversation_id,)
            ).fetchone()
        if row is None:
            raise NotFoundError(f"Chat {conversation_id} does not exist.")
        return Conversation(**row)

    def list(
        self, user_id: int, source_id: int | None = None, limit: int = 50
    ) -> list[Conversation]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                f"""
                {_SELECT}
                WHERE c.user_id = %(user)s
                  AND (%(source)s::bigint IS NULL OR c.source_id = %(source)s)
                ORDER BY c.updated_at DESC, c.id DESC LIMIT %(limit)s
                """,
                {"user": user_id, "source": source_id, "limit": limit},
            ).fetchall()
        return [Conversation(**row) for row in rows]

    def touch(self, conversation_id: int) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "UPDATE conversations SET updated_at = now() WHERE id = %s",
                (conversation_id,),
            )

    def delete(self, conversation_id: int) -> None:
        with self._pool.connection() as conn:
            conn.execute("DELETE FROM conversations WHERE id = %s", (conversation_id,))
