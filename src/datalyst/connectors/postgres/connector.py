import logging
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool, PoolTimeout

from datalyst.connectors.postgres.introspection import (
    COLUMNS_SQL,
    CONSTRAINTS_SQL,
    FINGERPRINTS_SQL,
    RELATION_KINDS,
    SAMPLE_MAX_DISTINCT,
    SAMPLE_VALUES_SQL,
    build_fingerprints,
    build_schema,
    relation_filter,
)
from datalyst.connectors.postgres.profiling import (
    COLUMN_STATS_SQL,
    FULL_SCAN_MAX_ROWS,
    RELATION_INFO_SQL,
    build_column_stats,
    containment_sql,
    identifier,
    sample_rows_sql,
    sampled_source,
    top_values_sql,
)
from datalyst.connectors.postgres.validator import PostgresQueryValidator
from datalyst.core.exceptions import QueryExecutionError
from datalyst.core.interfaces import DataSource, QueryValidator
from datalyst.core.models import (
    ColumnRef,
    ColumnStats,
    Containment,
    DataSchema,
    QueryResult,
    Table,
    TableFingerprint,
    TableKind,
    TableRef,
    ValueCount,
)

logger = logging.getLogger(__name__)

Query = str | sql.Composable


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
        sample_values: bool = True,
        validator: QueryValidator | None = None,
        pool_min_size: int = 1,
        pool_max_size: int = 5,
        connect_timeout_seconds: float = 10,
    ) -> None:
        self._url = url
        self._connect_timeout = connect_timeout_seconds
        self._timeout_ms = int(timeout_seconds * 1000)
        self._max_rows = max_rows
        self._include_schemas = set(include_schemas or [])
        self._sample_values = sample_values
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

    def check(self) -> None:
        # A direct connection reports why it failed (wrong password, unknown
        # database); the pool only reports that it timed out.
        try:
            with psycopg.connect(
                self._url, connect_timeout=max(1, int(self._connect_timeout))
            ) as conn:
                conn.read_only = True
                conn.execute("SELECT 1")
        except psycopg.Error as e:
            raise QueryExecutionError(_connection_problem(e)) from e

    def execute(self, query: str) -> QueryResult:
        start = time.perf_counter()
        try:
            with self._read_only() as cur:
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
        return DataSchema(tables=self.describe_tables())

    def fingerprints(self) -> list[TableFingerprint]:
        rows = self._fetch(FINGERPRINTS_SQL, self._filter(None), "read fingerprints")
        return build_fingerprints(rows)

    def describe_tables(self, tables: Sequence[TableRef] | None = None) -> list[Table]:
        if tables is not None and not tables:
            return []
        params = self._filter(tables)
        try:
            with self._read_only(dict_row) as cur:
                column_rows = cur.execute(COLUMNS_SQL, params).fetchall()
                constraint_rows = cur.execute(CONSTRAINTS_SQL, params).fetchall()
                sample_rows = (
                    self._sample_rows(cur, params) if self._sample_values else []
                )
        except (psycopg.Error, PoolTimeout) as e:
            raise QueryExecutionError(f"Could not read schema: {e}") from e
        return build_schema(column_rows, constraint_rows, sample_rows).tables

    def column_stats(self, table: TableRef) -> list[ColumnStats]:
        _, row_estimate = self._relation_info(table)
        rows = self._fetch(
            COLUMN_STATS_SQL,
            {"schema": table.schema_name, "table": table.name},
            f"read statistics for {table.qualified_name}",
        )
        return build_column_stats(rows, row_estimate)

    def sample_rows(self, table: TableRef, limit: int = 100) -> QueryResult:
        kind, row_estimate = self._relation_info(table)
        is_table = kind == TableKind.TABLE
        tablesampled = is_table and (row_estimate or 0) > FULL_SCAN_MAX_ROWS
        source = sampled_source(table, kind, row_estimate, limit)
        start = time.perf_counter()
        try:
            with self._read_only() as cur:
                query = sample_rows_sql(source, randomize=is_table and not tablesampled)
                cur.execute(query, {"limit": limit})
                columns = [d.name for d in cur.description or []]
                rows = cur.fetchall()
                if tablesampled and len(rows) < limit:
                    plain = sample_rows_sql(identifier(table), randomize=False)
                    cur.execute(plain, {"limit": limit})
                    rows = cur.fetchall()
        except (psycopg.Error, PoolTimeout) as e:
            raise QueryExecutionError(
                f"Could not sample {table.qualified_name}: {e}"
            ) from e
        return QueryResult(
            columns=columns,
            rows=[list(row) for row in rows],
            row_count=len(rows),
            elapsed_ms=round((time.perf_counter() - start) * 1000, 2),
        )

    def top_values(
        self, column: ColumnRef, limit: int = 50, scan_rows: int = 10_000
    ) -> list[ValueCount]:
        kind, row_estimate = self._relation_info(column.table)
        source = sampled_source(column.table, kind, row_estimate, scan_rows)
        rows = self._fetch(
            top_values_sql(source, column),
            {"limit": limit, "scan_rows": scan_rows},
            f"read values of {column.qualified_name}",
        )
        return [ValueCount(value=row["value"], count=row["count"]) for row in rows]

    def containment(
        self, child: ColumnRef, parent: ColumnRef, sample_size: int = 1000
    ) -> Containment:
        kind, row_estimate = self._relation_info(child.table)
        scan_rows = sample_size * 20
        source = sampled_source(child.table, kind, row_estimate, scan_rows)
        rows = self._fetch(
            containment_sql(source, child, parent),
            {"scan_rows": scan_rows, "sample_size": sample_size},
            f"compare {child.qualified_name} with {parent.qualified_name}",
        )
        return Containment(sampled=rows[0]["sampled"], matched=rows[0]["matched"])

    def close(self) -> None:
        self._pool.close()

    def _relation_info(self, table: TableRef) -> tuple[TableKind, int | None]:
        rows = self._fetch(
            RELATION_INFO_SQL,
            {"schema": table.schema_name, "table": table.name},
            f"find {table.qualified_name}",
        )
        if not rows:
            raise QueryExecutionError(f"Table {table.qualified_name} does not exist.")
        return RELATION_KINDS[rows[0]["relkind"]], rows[0]["row_estimate"]

    def _filter(self, tables: Sequence[TableRef] | None) -> dict[str, Any]:
        return relation_filter(self._include_schemas, tables)

    def _sample_rows(self, cur: psycopg.Cursor, params: dict[str, Any]) -> list[dict]:
        try:
            with cur.connection.transaction():
                cur.execute(
                    SAMPLE_VALUES_SQL, {**params, "max_distinct": SAMPLE_MAX_DISTINCT}
                )
                return cur.fetchall()
        except psycopg.Error as e:
            logger.warning("Skipping column sample values: %s", e)
            return []

    def _fetch(self, query: Query, params: dict[str, Any], action: str) -> list[dict]:
        try:
            with self._read_only(dict_row) as cur:
                return cur.execute(query, params).fetchall()
        except (psycopg.Error, PoolTimeout) as e:
            raise QueryExecutionError(f"Could not {action}: {e}") from e

    @contextmanager
    def _read_only(self, row_factory: Any = None) -> Iterator[psycopg.Cursor]:
        with (
            self._pool.connection() as conn,
            conn.transaction(),
            conn.cursor(row_factory=row_factory)
            if row_factory
            else conn.cursor() as cur,
        ):
            cur.execute(
                "SELECT set_config('statement_timeout', %s, true)",
                (str(self._timeout_ms),),
            )
            yield cur


def _connection_problem(error: psycopg.Error) -> str:
    text = " ".join(str(error).split())
    text = text.split(" Multiple connection attempts failed")[0]
    if "FATAL:" in text:
        reason = text.rsplit("FATAL:", 1)[-1]
    elif " failed: " in text:
        reason = text.split(" failed: ", 1)[-1]
    else:
        reason = text
    reason = reason.strip()
    return reason[:1].upper() + reason[1:] if reason else "Connection failed."


def _make_read_only(conn: psycopg.Connection) -> None:
    conn.read_only = True
