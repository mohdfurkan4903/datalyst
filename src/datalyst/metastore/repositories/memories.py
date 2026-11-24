from psycopg_pool import ConnectionPool

from datalyst.core.exceptions import NotFoundError
from datalyst.core.models import Memory, MemoryOrigin

MAX_LENGTH = 300

_COLUMNS = "id, user_id, source_id, text, origin, created_at, updated_at"


def normalize(text: str) -> str:
    return " ".join("".join(c.lower() if c.isalnum() else " " for c in text).split())


class MemoryRepository:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def list(self, user_id: int, source_id: int | None = None) -> list[Memory]:
        """A user's memories; with a source, those for every source and for it."""
        with self._pool.connection() as conn:
            rows = conn.execute(
                f"""
                SELECT {_COLUMNS} FROM memories
                WHERE user_id = %(user)s
                  AND (%(source)s::bigint IS NULL
                       OR source_id IS NULL OR source_id = %(source)s)
                ORDER BY updated_at DESC, id DESC
                """,
                {"user": user_id, "source": source_id},
            ).fetchall()
        return [Memory(**row) for row in rows]

    def get(self, user_id: int, memory_id: int) -> Memory:
        with self._pool.connection() as conn:
            row = conn.execute(
                f"SELECT {_COLUMNS} FROM memories WHERE id = %s AND user_id = %s",
                (memory_id, user_id),
            ).fetchone()
        if row is None:
            raise NotFoundError(f"Memory {memory_id} does not exist.")
        return Memory(**row)

    def add(
        self,
        user_id: int,
        text: str,
        source_id: int | None = None,
        origin: MemoryOrigin = MemoryOrigin.USER,
    ) -> Memory:
        """Save a memory; saying the same thing again refreshes the old one."""
        text = " ".join(text.split())[:MAX_LENGTH]
        wanted = normalize(text)
        for existing in self.list(user_id):
            if existing.source_id == source_id and normalize(existing.text) == wanted:
                with self._pool.connection() as conn:
                    conn.execute(
                        "UPDATE memories SET updated_at = now() WHERE id = %s",
                        (existing.id,),
                    )
                return self.get(user_id, existing.id)
        with self._pool.connection() as conn:
            row = conn.execute(
                "INSERT INTO memories (user_id, source_id, text, origin) "
                "VALUES (%s, %s, %s, %s) RETURNING id",
                (user_id, source_id, text, origin.value),
            ).fetchone()
        return self.get(user_id, row["id"])

    def update(self, user_id: int, memory_id: int, text: str) -> Memory:
        self.get(user_id, memory_id)
        with self._pool.connection() as conn:
            conn.execute(
                "UPDATE memories SET text = %s, updated_at = now() WHERE id = %s",
                (" ".join(text.split())[:MAX_LENGTH], memory_id),
            )
        return self.get(user_id, memory_id)

    def delete(self, user_id: int, memory_id: int) -> None:
        self.get(user_id, memory_id)
        with self._pool.connection() as conn:
            conn.execute("DELETE FROM memories WHERE id = %s", (memory_id,))

    def clear(self, user_id: int) -> int:
        with self._pool.connection() as conn:
            return conn.execute(
                "DELETE FROM memories WHERE user_id = %s", (user_id,)
            ).rowcount

    def count(self, user_id: int) -> int:
        with self._pool.connection() as conn:
            return conn.execute(
                "SELECT count(*) AS n FROM memories WHERE user_id = %s", (user_id,)
            ).fetchone()["n"]

    def drop_oldest_learned(self, user_id: int) -> bool:
        """Make room by removing the oldest memory the assistant saved itself."""
        with self._pool.connection() as conn:
            return (
                conn.execute(
                    """
                    DELETE FROM memories WHERE id = (
                        SELECT id FROM memories
                        WHERE user_id = %s AND origin = 'agent'
                        ORDER BY updated_at, id LIMIT 1)
                    """,
                    (user_id,),
                ).rowcount
                > 0
            )
