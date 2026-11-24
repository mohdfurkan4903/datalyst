import hashlib
import re
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import urlsplit

import duckdb

from datalyst.connectors.files.profiling import (
    containment_sql,
    identifier,
    quote,
    sample_rows_sql,
    top_values_sql,
)
from datalyst.connectors.files.validator import FilesQueryValidator
from datalyst.core.exceptions import ConfigurationError, QueryExecutionError
from datalyst.core.interfaces import DataSource, QueryValidator
from datalyst.core.models import (
    Column,
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

SCHEMA = "main"
FOLDER = re.compile(r"^[0-9a-f]{32}$")
TABLE_FILE_SUFFIX = ".parquet"
MEMORY_LIMIT = "1GB"

COLUMNS_SQL = """
SELECT table_name, column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_schema = 'main'
ORDER BY table_name, ordinal_position
"""


class FilesDataSource(DataSource):
    """Uploaded files, kept as one Parquet file per table in a folder and queried
    with DuckDB. The URL is ``files://<folder name>``."""

    dialect = "DuckDB"
    query_language = "SQL"
    sql_dialect = "duckdb"
    default_schema = SCHEMA
    text_date_function = "strptime(column, '<format such as %d/%m/%Y>')"

    def __init__(
        self,
        url: str,
        files_dir: Path,
        *,
        timeout_seconds: float = 30,
        max_rows: int = 1000,
        validator: QueryValidator | None = None,
    ) -> None:
        folder = urlsplit(url).netloc or urlsplit(url).path.strip("/")
        if not FOLDER.match(folder):
            raise ConfigurationError("Not an uploaded-files data source.")
        self.directory = (Path(files_dir) / folder).resolve()
        self._timeout = timeout_seconds
        self._max_rows = max_rows
        self._validator = validator or FilesQueryValidator()
        self._lock = threading.Lock()
        self._signature: tuple | None = None
        self._tables: dict[str, Path] = {}
        self._db = duckdb.connect(":memory:")
        # Questions can read this folder and nothing else on the machine.
        self._db.execute(f"SET memory_limit = '{MEMORY_LIMIT}'")
        self._db.execute(
            f"SET allowed_directories = ['{_literal(str(self.directory))}']"
        )
        self._db.execute("SET enable_external_access = false")
        self._db.execute("SET autoinstall_known_extensions = false")
        self._db.execute("SET autoload_known_extensions = false")
        self._db.execute("SET lock_configuration = true")

    def validate(self, query: str) -> str:
        return self._validator.validate(query)

    def check(self) -> None:
        if not self.directory.is_dir():
            raise QueryExecutionError("The uploaded files are missing on this server.")
        self._refresh()

    def execute(self, query: str) -> QueryResult:
        start = time.perf_counter()
        self._refresh()
        cursor = self._db.cursor()
        timer = threading.Timer(self._timeout, cursor.interrupt)
        timer.start()
        try:
            cursor.execute(query)
            columns = [d[0] for d in cursor.description or []]
            rows = cursor.fetchmany(self._max_rows + 1) if cursor.description else []
        except duckdb.InterruptException as e:
            raise QueryExecutionError(
                f"The query ran longer than {self._timeout:g} seconds and was stopped."
            ) from e
        except duckdb.Error as e:
            raise QueryExecutionError(_message(e)) from e
        finally:
            timer.cancel()
            cursor.close()

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
        self._refresh()
        columns = self._columns()
        found = []
        for name, path in sorted(self._tables.items()):
            stat = path.stat()
            text = "|".join(
                [
                    ",".join(f"{c.name}:{c.data_type}" for c in columns.get(name, [])),
                    str(stat.st_size),
                    str(stat.st_mtime_ns),
                ]
            )
            found.append(
                TableFingerprint(
                    table=TableRef(schema_name=SCHEMA, name=name),
                    kind=TableKind.TABLE,
                    fingerprint=hashlib.md5(text.encode()).hexdigest(),
                    row_estimate=self._count(name),
                )
            )
        return found

    def describe_tables(self, tables: Sequence[TableRef] | None = None) -> list[Table]:
        self._refresh()
        columns = self._columns()
        wanted = None if tables is None else {t.name for t in tables}
        return [
            Table(
                schema_name=SCHEMA,
                name=name,
                row_estimate=self._count(name),
                columns=columns.get(name, []),
            )
            for name in sorted(self._tables)
            if wanted is None or name in wanted
        ]

    def column_stats(self, table: TableRef) -> list[ColumnStats]:
        return []

    def sample_rows(self, table: TableRef, limit: int = 100) -> QueryResult:
        return self._fetch_result(sample_rows_sql(table, limit), f"sample {table.name}")

    def top_values(
        self, column: ColumnRef, limit: int = 50, scan_rows: int = 10_000
    ) -> list[ValueCount]:
        rows = self._run(
            top_values_sql(column, scan_rows, limit),
            f"read values of {column.qualified_name}",
        )
        return [ValueCount(value=value, count=count) for value, count in rows]

    def containment(
        self, child: ColumnRef, parent: ColumnRef, sample_size: int = 1000
    ) -> Containment:
        rows = self._run(
            containment_sql(child, parent, sample_size * 20, sample_size),
            f"compare {child.qualified_name} with {parent.qualified_name}",
        )
        return Containment(sampled=rows[0][0], matched=rows[0][1])

    def close(self) -> None:
        self._db.close()

    def _refresh(self) -> None:
        """Make the tables match the Parquet files now in the folder."""
        found = {
            path.stem: path
            for path in sorted(self.directory.glob(f"*{TABLE_FILE_SUFFIX}"))
        }
        signature = tuple(
            (name, path.stat().st_mtime_ns, path.stat().st_size)
            for name, path in found.items()
        )
        with self._lock:
            if signature == self._signature:
                return
            try:
                for name in self._tables.keys() - found.keys():
                    self._db.execute(f"DROP VIEW IF EXISTS {quote(name)}")
                for name, path in found.items():
                    self._db.execute(
                        f"CREATE OR REPLACE VIEW {quote(name)} AS "
                        f"SELECT * FROM read_parquet('{_literal(str(path))}')"
                    )
            except duckdb.Error as e:
                raise QueryExecutionError(
                    f"Could not read the uploaded files: {_message(e)}"
                ) from e
            self._tables = found
            self._signature = signature

    def _columns(self) -> dict[str, list[Column]]:
        by_table: dict[str, list[Column]] = {}
        for table, column, data_type, nullable in self._run(
            COLUMNS_SQL, "read the columns"
        ):
            by_table.setdefault(table, []).append(
                Column(name=column, data_type=data_type, nullable=nullable == "YES")
            )
        return by_table

    def _count(self, name: str) -> int:
        return self._run(
            f"SELECT COUNT(*) FROM {identifier(TableRef(schema_name=SCHEMA, name=name))}",
            f"count {name}",
        )[0][0]

    def _run(self, query: str, action: str) -> list[tuple]:
        self._refresh()
        cursor = self._db.cursor()
        timer = threading.Timer(self._timeout, cursor.interrupt)
        timer.start()
        try:
            return cursor.execute(query).fetchall()
        except duckdb.Error as e:
            raise QueryExecutionError(f"Could not {action}: {_message(e)}") from e
        finally:
            timer.cancel()
            cursor.close()

    def _fetch_result(self, query: str, action: str) -> QueryResult:
        start = time.perf_counter()
        self._refresh()
        cursor = self._db.cursor()
        timer = threading.Timer(self._timeout, cursor.interrupt)
        timer.start()
        try:
            cursor.execute(query)
            columns = [d[0] for d in cursor.description or []]
            rows = cursor.fetchall()
        except duckdb.Error as e:
            raise QueryExecutionError(f"Could not {action}: {_message(e)}") from e
        finally:
            timer.cancel()
            cursor.close()
        return QueryResult(
            columns=columns,
            rows=[list(row) for row in rows],
            row_count=len(rows),
            elapsed_ms=round((time.perf_counter() - start) * 1000, 2),
        )


def _literal(text: str) -> str:
    return text.replace("'", "''")


def _message(error: duckdb.Error) -> str:
    text = " ".join(str(error).split())
    return text[:1].upper() + text[1:] if text else "The query failed."
