import time

import psycopg
from psycopg_pool import ConnectionPool, PoolTimeout

from datalyst.connectors.postgres.introspection import (
    COLUMNS_SQL,
    CONSTRAINTS_SQL,
    build_schema,
)
from datalyst.connectors.postgres.validator import PostgresQueryValidator
from datalyst.core.exceptions import QueryExecutionError
from datalyst.core.interfaces import DataSource, QueryValidator
from datalyst.core.models import DataSchema, QueryResult


class PostgresDataSource(DataSource):
    dialect = "PostgreSQL"
    query_language = "SQL"

    def __init__(
        self,
        url: str,
        *,
        timeout_seconds: float = 30,
        max_rows: int = 1000,
        include_schemas: list[str] | None = None,
        validator: QueryValidator | None = None,
        pool_min_size: int = 1,
        pool_max_size: int = 5,
        connect_timeout_seconds: float = 10,
    ) -> None:
        self._timeout_ms = int(timeout_seconds * 1000)
        self._max_rows = max_rows
        self._include_schemas = set(include_schemas or [])
        self._validator = validator or PostgresQueryValidator()
        self._pool = ConnectionPool(
            url,
            min_size=pool_min_size,
            max_size=pool_max_size,
            timeout=connect_timeout_seconds,
            configure=_make_read_only,
            open=True,
        )

    def validate(self, query: str) -> str:
        return self._validator.validate(query)

    def execute(self, query: str) -> QueryResult:
        start = time.perf_counter()
        try:
            with (
                self._pool.connection() as conn,
                conn.transaction(),
                conn.cursor() as cur,
            ):
                cur.execute(
                    "SELECT set_config('statement_timeout', %s, true)",
                    (str(self._timeout_ms),),
                )
                cur.execute(query)
                columns = [d.name for d in cur.description or []]
                rows = cur.fetchmany(self._max_rows + 1) if cur.description else []
        except (psycopg.Error, PoolTimeout) as e:
            raise QueryExecutionError(str(e).strip()) from e

        return QueryResult(
            columns=columns,
            rows=[list(row) for row in rows[: self._max_rows]],
            row_count=min(len(rows), self._max_rows),
            truncated=len(rows) > self._max_rows,
            elapsed_ms=round((time.perf_counter() - start) * 1000, 2),
        )

    def get_schema(self) -> DataSchema:
        try:
            with self._pool.connection() as conn, conn.cursor() as cur:
                column_rows = cur.execute(COLUMNS_SQL).fetchall()
                constraint_rows = cur.execute(CONSTRAINTS_SQL).fetchall()
        except (psycopg.Error, PoolTimeout) as e:
            raise QueryExecutionError(f"Could not read schema: {e}") from e

        return build_schema(column_rows, constraint_rows, self._include_schemas)

    def close(self) -> None:
        self._pool.close()


def _make_read_only(conn: psycopg.Connection) -> None:
    conn.read_only = True
