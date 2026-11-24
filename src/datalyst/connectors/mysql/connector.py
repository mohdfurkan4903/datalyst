import queue
import threading
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

import pymysql
from pymysql.cursors import Cursor, DictCursor, SSCursor

from datalyst.connectors.mysql.introspection import (
    RELATION_INFO_SQL,
    TABLE_KINDS,
    build_fingerprints,
    build_schema,
    columns_sql,
    constraints_sql,
    scope,
    tables_sql,
)
from datalyst.connectors.mysql.profiling import (
    containment_sql,
    sample_rows_sql,
    top_values_sql,
)
from datalyst.connectors.mysql.validator import MySQLQueryValidator
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

DEFAULT_PORT = 3306


@dataclass(frozen=True)
class Connection:
    host: str
    port: int
    user: str | None
    password: str | None
    database: str | None
    ssl: bool

    @classmethod
    def parse(cls, url: str) -> "Connection":
        parts = urlsplit(url)
        options = {k: v[-1].lower() for k, v in parse_qs(parts.query).items()}
        return cls(
            host=parts.hostname or "localhost",
            port=parts.port or DEFAULT_PORT,
            user=unquote(parts.username) if parts.username else None,
            password=unquote(parts.password) if parts.password else None,
            database=parts.path.lstrip("/") or None,
            ssl=options.get("ssl") in {"1", "true", "yes", "required"},
        )


class MySQLDataSource(DataSource):
    dialect = "MySQL"
    query_language = "SQL"
    sql_dialect = "mysql"
    text_date_function = "STR_TO_DATE(column, '<format such as %d/%m/%Y>')"

    def __init__(
        self,
        url: str,
        *,
        timeout_seconds: float = 30,
        max_rows: int = 1000,
        include_schemas: list[str] | None = None,
        validator: QueryValidator | None = None,
        pool_max_size: int = 5,
        connect_timeout_seconds: float = 10,
    ) -> None:
        self._target = Connection.parse(url)
        self._timeout_ms = int(timeout_seconds * 1000)
        self._max_rows = max_rows
        self._connect_timeout = connect_timeout_seconds
        self._schemas = list(
            include_schemas
            or ([self._target.database] if self._target.database else [])
        )
        self.default_schema = self._target.database or (
            self._schemas[0] if self._schemas else ""
        )
        self._validator = validator or MySQLQueryValidator()
        self._pool = _Pool(self._connect, pool_max_size, connect_timeout_seconds)

    def validate(self, query: str) -> str:
        return self._validator.validate(query)

    def check(self) -> None:
        # A direct connection reports why it failed (wrong password, unknown
        # database); the pool only reports that it timed out.
        try:
            conn = self._connect()
        except pymysql.MySQLError as e:
            raise QueryExecutionError(_message(e)) from e
        conn.close()

    def execute(self, query: str) -> QueryResult:
        start = time.perf_counter()
        try:
            with self._pool.lease() as lease:
                cur = lease.connection.cursor(SSCursor)
                cur.execute(query)
                columns = [d[0] for d in cur.description or []]
                rows = (
                    list(cur.fetchmany(self._max_rows + 1)) if cur.description else []
                )
                if len(rows) > self._max_rows:
                    # The rest of a streamed result would have to be read before the
                    # connection could be reused, so drop the connection instead.
                    cur._result.unbuffered_active = False  # nothing left to finish
                    lease.discard()
        except pymysql.MySQLError as e:
            raise QueryExecutionError(_message(e)) from e

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
        tables, columns, constraints = self._read_catalog(None)
        return build_fingerprints(tables, columns, constraints)

    def describe_tables(self, tables: Sequence[TableRef] | None = None) -> list[Table]:
        if tables is not None and not tables:
            return []
        return build_schema(*self._read_catalog(tables)).tables

    def column_stats(self, table: TableRef) -> list[ColumnStats]:
        # MySQL keeps no per-column statistics the way PostgreSQL does, so the
        # profile is computed from the sampled rows alone.
        return []

    def sample_rows(self, table: TableRef, limit: int = 100) -> QueryResult:
        kind, row_estimate = self._relation_info(table)
        query, params = sample_rows_sql(table, kind, row_estimate, limit)
        return self._fetch_result(query, params, f"sample {table.qualified_name}")

    def top_values(
        self, column: ColumnRef, limit: int = 50, scan_rows: int = 10_000
    ) -> list[ValueCount]:
        query, params = top_values_sql(column, scan_rows, limit)
        rows = self._fetch(query, params, f"read values of {column.qualified_name}")
        return [ValueCount(value=r["value"], count=int(r["count"])) for r in rows]

    def containment(
        self, child: ColumnRef, parent: ColumnRef, sample_size: int = 1000
    ) -> Containment:
        query, params = containment_sql(child, parent, sample_size * 20, sample_size)
        rows = self._fetch(
            query,
            params,
            f"compare {child.qualified_name} with {parent.qualified_name}",
        )
        return Containment(
            sampled=int(rows[0]["sampled"]), matched=int(rows[0]["matched"])
        )

    def close(self) -> None:
        self._pool.close()

    def _connect(self) -> pymysql.Connection:
        target = self._target
        return pymysql.connect(
            host=target.host,
            port=target.port,
            user=target.user,
            password=target.password,
            database=target.database,
            ssl={} if target.ssl else None,
            charset="utf8mb4",
            autocommit=True,
            connect_timeout=max(1, int(self._connect_timeout)),
            # Writes fail at the server, whatever the validator lets through.
            init_command=(
                "SET SESSION transaction_read_only = 1, "
                f"SESSION max_execution_time = {self._timeout_ms}"
            ),
        )

    def _read_catalog(
        self, tables: Sequence[TableRef] | None
    ) -> tuple[list[dict], list[dict], list[dict]]:
        where, params = scope(self._schemas, tables)
        try:
            with self._pool.lease() as lease:
                cur = lease.connection.cursor(DictCursor)
                results = []
                for query in (
                    tables_sql(where),
                    columns_sql(where),
                    constraints_sql(where),
                ):
                    cur.execute(query, params)
                    results.append(list(cur.fetchall()))
        except pymysql.MySQLError as e:
            raise QueryExecutionError(f"Could not read schema: {_message(e)}") from e
        return results[0], results[1], results[2]

    def _relation_info(self, table: TableRef) -> tuple[TableKind, int | None]:
        rows = self._fetch(
            RELATION_INFO_SQL,
            [table.schema_name, table.name],
            f"find {table.qualified_name}",
        )
        if not rows or rows[0]["table_type"] not in TABLE_KINDS:
            raise QueryExecutionError(f"Table {table.qualified_name} does not exist.")
        estimate = rows[0]["row_estimate"]
        return TABLE_KINDS[rows[0]["table_type"]], None if estimate is None else int(
            estimate
        )

    def _fetch(self, query: str, params: Sequence[Any], action: str) -> list[dict]:
        try:
            with self._pool.lease() as lease:
                cur = lease.connection.cursor(DictCursor)
                cur.execute(query, params)
                return list(cur.fetchall())
        except pymysql.MySQLError as e:
            raise QueryExecutionError(f"Could not {action}: {_message(e)}") from e

    def _fetch_result(
        self, query: str, params: Sequence[Any], action: str
    ) -> QueryResult:
        start = time.perf_counter()
        try:
            with self._pool.lease() as lease:
                cur: Cursor = lease.connection.cursor()
                cur.execute(query, params)
                columns = [d[0] for d in cur.description or []]
                rows = list(cur.fetchall())
        except pymysql.MySQLError as e:
            raise QueryExecutionError(f"Could not {action}: {_message(e)}") from e
        return QueryResult(
            columns=columns,
            rows=[list(row) for row in rows],
            row_count=len(rows),
            elapsed_ms=round((time.perf_counter() - start) * 1000, 2),
        )


class _Lease:
    def __init__(self, connection: pymysql.Connection) -> None:
        self.connection = connection
        self.broken = False

    def discard(self) -> None:
        self.broken = True


class _Pool:
    """A few reusable connections; PyMySQL ships none of its own."""

    def __init__(self, connect, size: int, wait_seconds: float) -> None:
        self._connect = connect
        self._wait = wait_seconds
        self._idle: queue.LifoQueue[pymysql.Connection] = queue.LifoQueue()
        self._slots = threading.BoundedSemaphore(size)
        self._closed = False

    @contextmanager
    def lease(self) -> Iterator[_Lease]:
        if not self._slots.acquire(timeout=self._wait):
            raise pymysql.err.OperationalError(
                0, "All database connections are busy, try again."
            )
        lease = None
        try:
            lease = _Lease(self._take())
            yield lease
        except (pymysql.err.OperationalError, pymysql.err.InterfaceError):
            if lease:
                lease.discard()
            raise
        finally:
            if lease:
                self._give_back(lease)
            self._slots.release()

    def close(self) -> None:
        self._closed = True
        while True:
            try:
                self._idle.get_nowait().close()
            except queue.Empty:
                return
            except pymysql.MySQLError:
                continue

    def _take(self) -> pymysql.Connection:
        while True:
            try:
                conn = self._idle.get_nowait()
            except queue.Empty:
                return self._connect()
            try:
                conn.ping(reconnect=False)
                return conn
            except pymysql.MySQLError:
                _quietly_close(conn)

    def _give_back(self, lease: _Lease) -> None:
        if lease.broken or self._closed:
            _quietly_close(lease.connection)
        else:
            self._idle.put(lease.connection)


def _quietly_close(conn: pymysql.Connection) -> None:
    try:
        conn.close()
    except pymysql.MySQLError:
        pass


def _message(error: pymysql.MySQLError) -> str:
    text = str(error.args[1]) if len(error.args) > 1 else str(error)
    text = " ".join(text.split())
    return text[:1].upper() + text[1:] if text else "Connection failed."
