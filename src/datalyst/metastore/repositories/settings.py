from collections.abc import Mapping
from typing import Any

from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from datalyst.core.exceptions import ConfigurationError
from datalyst.security import SecretBox


class SettingsRepository:
    """Settings edited in the UI. Secret values are stored encrypted."""

    def __init__(self, pool: ConnectionPool, secrets: SecretBox | None) -> None:
        self._pool = pool
        self._secrets = secrets

    def version(self) -> int:
        with self._pool.connection() as conn:
            row = conn.execute("SELECT version FROM settings_version").fetchone()
        return row["version"] if row else 0

    def values(self) -> dict[str, Any]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT name, value, encrypted FROM settings"
            ).fetchall()
        values = {}
        for row in rows:
            if row["encrypted"] is not None:
                values[row["name"]] = self._box().decrypt(row["encrypted"])
            else:
                values[row["name"]] = row["value"]
        return values

    def save(
        self,
        values: Mapping[str, Any],
        secret_names: set[str],
        removed: set[str] = frozenset(),
        user_id: int | None = None,
    ) -> int:
        """Store values, delete ``removed`` names and return the new version."""
        with self._pool.connection() as conn, conn.transaction():
            for name, value in values.items():
                if name in secret_names:
                    stored, encrypted = None, self._box().encrypt(str(value))
                else:
                    stored, encrypted = Jsonb(value), None
                conn.execute(
                    """
                    INSERT INTO settings (name, value, encrypted, updated_by)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (name) DO UPDATE SET
                        value = EXCLUDED.value,
                        encrypted = EXCLUDED.encrypted,
                        updated_by = EXCLUDED.updated_by,
                        updated_at = now()
                    """,
                    (name, stored, encrypted, user_id),
                )
            if removed:
                conn.execute(
                    "DELETE FROM settings WHERE name = ANY(%s)", (list(removed),)
                )
            row = conn.execute(
                "UPDATE settings_version SET version = version + 1 RETURNING version"
            ).fetchone()
        return row["version"]

    def _box(self) -> SecretBox:
        if self._secrets is None:
            raise ConfigurationError(
                "Set DATALYST_SECRET_KEY to store API keys. "
                "Create one with: datalyst secret-key"
            )
        return self._secrets
