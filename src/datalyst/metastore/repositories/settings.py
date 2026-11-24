from collections.abc import Mapping
from typing import Any

from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool


class SettingsRepository:
    """Settings edited in the UI."""

    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def version(self) -> int:
        with self._pool.connection() as conn:
            row = conn.execute("SELECT version FROM settings_version").fetchone()
        return row["version"] if row else 0

    def values(self) -> dict[str, Any]:
        with self._pool.connection() as conn:
            rows = conn.execute("SELECT name, value FROM settings").fetchall()
        return {row["name"]: row["value"] for row in rows}

    def save(
        self,
        values: Mapping[str, Any],
        removed: set[str] = frozenset(),
        user_id: int | None = None,
    ) -> int:
        """Store values, delete ``removed`` names and return the new version."""
        with self._pool.connection() as conn, conn.transaction():
            for name, value in values.items():
                conn.execute(
                    """
                    INSERT INTO settings (name, value, updated_by)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (name) DO UPDATE SET
                        value = EXCLUDED.value,
                        updated_by = EXCLUDED.updated_by,
                        updated_at = now()
                    """,
                    (name, Jsonb(value), user_id),
                )
            if removed:
                conn.execute(
                    "DELETE FROM settings WHERE name = ANY(%s)", (list(removed),)
                )
            row = conn.execute(
                "UPDATE settings_version SET version = version + 1 RETURNING version"
            ).fetchone()
        return row["version"]
