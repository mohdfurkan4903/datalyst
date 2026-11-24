import re

from sqlglot import exp

from datalyst.connectors.validation import SelectOnlyValidator
from datalyst.core.exceptions import UnsafeQueryError

FORBIDDEN_FUNCTIONS = frozenset(
    {
        "getenv",
        "current_setting",
        "read_text",
        "read_blob",
        "read_csv",
        "read_csv_auto",
        "read_json",
        "read_json_auto",
        "read_parquet",
        "parquet_scan",
        "glob",
        "sniff_csv",
    }
)

TABLE_NAME = re.compile(r"\w+")

# Row generators are the only table functions a question may use.
ALLOWED_TABLE_FUNCTIONS = (exp.GenerateSeries, exp.Unnest, exp.Values)


class FilesQueryValidator(SelectOnlyValidator):
    """Questions may read the uploaded tables and nothing else: no file, setting
    or extension access. DuckDB itself is also locked to the source's folder."""

    def __init__(self) -> None:
        super().__init__("duckdb", FORBIDDEN_FUNCTIONS)

    def validate(self, query: str) -> str:
        sql = super().validate(query)
        root = self._parse_single_statement(sql)
        for table in root.find_all(exp.Table):
            if isinstance(table.this, exp.Identifier):
                # A quoted path such as "/etc/passwd" is DuckDB's way to read a file.
                if not TABLE_NAME.fullmatch(table.name):
                    raise UnsafeQueryError(
                        f"Only the uploaded tables can be queried, not {table.name[:40]}."
                    )
            elif not isinstance(table.this, ALLOWED_TABLE_FUNCTIONS):
                raise UnsafeQueryError(
                    f"Only the uploaded tables can be queried, not {table.this.sql('duckdb')[:40]}."
                )
        return sql
