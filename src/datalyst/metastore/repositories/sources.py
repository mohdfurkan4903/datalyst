from typing import Any

import psycopg
from psycopg_pool import ConnectionPool

from datalyst.core.exceptions import ConfigurationError, NotFoundError
from datalyst.core.models import DataSourceInfo, LLMDataPolicy, SourceStatus

_COLUMNS = """
id, name, kind, include_schemas, llm_data_policy, open_to_all,
sync_interval_seconds, catalog_version, status, status_detail, last_synced_at,
last_checked_at, next_check_at, created_at
"""


class SourceRepository:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def create(
        self,
        name: str,
        kind: str,
        connection_url: str,
        *,
        include_schemas: list[str] | None = None,
        llm_data_policy: LLMDataPolicy = LLMDataPolicy.ROWS,
        sync_interval_seconds: int = 300,
    ) -> DataSourceInfo:
        try:
            with self._pool.connection() as conn:
                row = conn.execute(
                    f"""
                    INSERT INTO data_sources (
                        name, kind, connection_url, include_schemas,
                        llm_data_policy, sync_interval_seconds
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING {_COLUMNS}
                    """,
                    (
                        name,
                        kind,
                        connection_url,
                        include_schemas or [],
                        llm_data_policy.value,
                        sync_interval_seconds,
                    ),
                ).fetchone()
        except psycopg.errors.UniqueViolation as e:
            raise ConfigurationError(f"A data source named '{name}' exists.") from e
        except psycopg.errors.CheckViolation as e:
            raise ConfigurationError(f"Invalid data source settings: {e}") from e
        return DataSourceInfo(**row)

    def all(self) -> list[DataSourceInfo]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                f"SELECT {_COLUMNS} FROM data_sources ORDER BY name"
            ).fetchall()
        return [DataSourceInfo(**row) for row in rows]

    def get(self, source_id: int) -> DataSourceInfo:
        return self._one("id = %s", source_id)

    def get_by_name(self, name: str) -> DataSourceInfo:
        return self._one("name = %s", name)

    def connection_url(self, source_id: int) -> str:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT connection_url FROM data_sources WHERE id = %s",
                (source_id,),
            ).fetchone()
        if row is None:
            raise NotFoundError(f"Data source {source_id} does not exist.")
        return row["connection_url"]

    def update(self, source_id: int, **fields: Any) -> DataSourceInfo:
        allowed = {"include_schemas", "llm_data_policy", "sync_interval_seconds"}
        unknown = set(fields) - allowed
        if unknown:
            raise ConfigurationError(f"Cannot update: {', '.join(sorted(unknown))}")
        values = {
            key: value.value if isinstance(value, LLMDataPolicy) else value
            for key, value in fields.items()
        }
        if not values:
            return self.get(source_id)
        assignments = ", ".join(f"{key} = %({key})s" for key in values)
        with self._pool.connection() as conn:
            row = conn.execute(
                f"""
                UPDATE data_sources SET {assignments}, updated_at = now()
                WHERE id = %(id)s RETURNING {_COLUMNS}
                """,
                {**values, "id": source_id},
            ).fetchone()
        if row is None:
            raise NotFoundError(f"Data source {source_id} does not exist.")
        return DataSourceInfo(**row)

    def set_connection_url(self, source_id: int, connection_url: str) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                """
                UPDATE data_sources
                SET connection_url = %s, updated_at = now() WHERE id = %s
                """,
                (connection_url, source_id),
            )

    def access(self, source_id: int) -> tuple[bool, list[int]]:
        """Whether every user may use the source, and the users picked otherwise."""
        info = self.get(source_id)
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT user_id FROM source_users WHERE source_id = %s ORDER BY user_id",
                (source_id,),
            ).fetchall()
        return info.open_to_all, [row["user_id"] for row in rows]

    def set_access(
        self, source_id: int, open_to_all: bool, user_ids: list[int]
    ) -> None:
        with self._pool.connection() as conn, conn.transaction():
            updated = conn.execute(
                """
                UPDATE data_sources SET open_to_all = %s, updated_at = now()
                WHERE id = %s
                """,
                (open_to_all, source_id),
            ).rowcount
            if not updated:
                raise NotFoundError(f"Data source {source_id} does not exist.")
            conn.execute("DELETE FROM source_users WHERE source_id = %s", (source_id,))
            try:
                with conn.cursor() as cur:
                    cur.executemany(
                        "INSERT INTO source_users (source_id, user_id) VALUES (%s, %s)",
                        [(source_id, user_id) for user_id in dict.fromkeys(user_ids)],
                    )
            except psycopg.errors.ForeignKeyViolation as e:
                raise NotFoundError("One of those users does not exist.") from e

    def usable_by(self, user_id: int) -> set[int]:
        """Sources a non-admin user may use."""
        with self._pool.connection() as conn:
            rows = conn.execute(
                """
                SELECT id FROM data_sources WHERE open_to_all
                UNION
                SELECT source_id FROM source_users WHERE user_id = %s
                """,
                (user_id,),
            ).fetchall()
        return {row["id"] for row in rows}

    def set_status(
        self, source_id: int, status: SourceStatus, detail: str | None = None
    ) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                """
                UPDATE data_sources
                SET status = %s, status_detail = %s, updated_at = now() WHERE id = %s
                """,
                (status.value, detail, source_id),
            )

    def due_for_check(self, limit: int = 100) -> list[int]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """
                UPDATE data_sources s
                SET next_check_at = now() + make_interval(secs => s.sync_interval_seconds)
                WHERE s.id IN (
                    SELECT id FROM data_sources
                    WHERE next_check_at <= now()
                    ORDER BY next_check_at
                    FOR UPDATE SKIP LOCKED
                    LIMIT %s
                )
                RETURNING s.id
                """,
                (limit,),
            ).fetchall()
        return [row["id"] for row in rows]

    def request_check(self, source_id: int) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "UPDATE data_sources SET next_check_at = now() WHERE id = %s",
                (source_id,),
            )

    def mark_checked(self, source_id: int) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                """
                UPDATE data_sources
                SET last_checked_at = now(), status = 'ready', status_detail = NULL,
                    updated_at = now()
                WHERE id = %s
                """,
                (source_id,),
            )

    def delete(self, source_id: int) -> None:
        with self._pool.connection() as conn:
            deleted = conn.execute(
                "DELETE FROM data_sources WHERE id = %s", (source_id,)
            ).rowcount
        if not deleted:
            raise NotFoundError(f"Data source {source_id} does not exist.")

    def _one(self, condition: str, value: object) -> DataSourceInfo:
        with self._pool.connection() as conn:
            row = conn.execute(
                f"SELECT {_COLUMNS} FROM data_sources WHERE {condition}", (value,)
            ).fetchone()
        if row is None:
            raise NotFoundError(f"Data source '{value}' does not exist.")
        return DataSourceInfo(**row)
