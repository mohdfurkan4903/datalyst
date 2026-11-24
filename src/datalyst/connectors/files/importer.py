"""Turn an uploaded file into Parquet tables that DuckDB can query fast.

Types are read from the data once, here, so every later query and profile sees
real numbers and dates instead of text.
"""

import os
import re
from dataclasses import dataclass
from pathlib import Path

import duckdb

from datalyst.connectors.files.connector import TABLE_FILE_SUFFIX
from datalyst.connectors.files.profiling import quote
from datalyst.core.exceptions import ConfigurationError, brief

CSV_SUFFIXES = frozenset({".csv", ".tsv", ".txt"})
MAX_NAME_LENGTH = 63

# Tried in turn: let DuckDB pick the types, then keep everything as text, then
# also accept older encodings and uneven rows.
CSV_ATTEMPTS = (
    "",
    ", all_varchar = true",
    ", all_varchar = true, encoding = 'latin-1'",
    ", all_varchar = true, encoding = 'latin-1', null_padding = true, ignore_errors = true",
)


@dataclass(frozen=True)
class ImportedTable:
    name: str
    rows: int
    columns: list[str]
    replaced: bool = False


def supported_suffixes() -> frozenset[str]:
    return CSV_SUFFIXES


def table_name(text: str) -> str:
    """A safe table name from a file name or sheet name."""
    name = re.sub(r"[^a-z0-9]+", "_", Path(text).stem.lower()).strip("_")
    if not name or name[0].isdigit():
        name = f"t_{name}" if name else "table"
    return name[:MAX_NAME_LENGTH]


def column_names(names: list[str]) -> list[str]:
    """Clean, unique, SQL-friendly column names, in the original order."""
    clean, seen = [], set()
    for position, original in enumerate(names, start=1):
        name = re.sub(r"[^a-z0-9]+", "_", str(original).lower()).strip("_")
        if not name:
            name = f"column_{position}"
        elif name[0].isdigit():
            name = f"c_{name}"
        name = name[:MAX_NAME_LENGTH]
        unique, count = name, 1
        while unique in seen:
            count += 1
            unique = f"{name}_{count}"
        seen.add(unique)
        clean.append(unique)
    return clean


def import_file(source: Path, filename: str, directory: Path) -> list[ImportedTable]:
    suffix = Path(filename).suffix.lower()
    if suffix in CSV_SUFFIXES:
        return [import_csv(source, table_name(filename), filename, directory)]
    raise ConfigurationError(
        f"Cannot read '{filename}'. Upload a CSV file ({', '.join(sorted(CSV_SUFFIXES))})."
    )


def import_csv(
    source: Path, table: str, filename: str, directory: Path
) -> ImportedTable:
    directory.mkdir(parents=True, exist_ok=True)
    pending = directory / f".{table}{TABLE_FILE_SUFFIX}.tmp"
    db = duckdb.connect(":memory:")
    problem: duckdb.Error | None = None
    try:
        for options in CSV_ATTEMPTS:
            relation = f"read_csv('{_literal(source)}'{options})"
            try:
                described = db.execute(f"DESCRIBE SELECT * FROM {relation}").fetchall()
                original = [row[0] for row in described]
                names = column_names(original)
                select = ", ".join(
                    f"{quote(old)} AS {quote(new)}"
                    for old, new in zip(original, names, strict=True)
                )
                db.execute(
                    f"COPY (SELECT {select} FROM {relation}) TO '{_literal(pending)}' "
                    "(FORMAT parquet, COMPRESSION zstd)"
                )
                break
            except duckdb.Error as e:
                problem = e
                pending.unlink(missing_ok=True)
        else:
            raise ConfigurationError(
                f"Could not read '{filename}' as a CSV file: {brief(problem or '')}"
            )
        rows = db.execute(
            f"SELECT COUNT(*) FROM read_parquet('{_literal(pending)}')"
        ).fetchone()[0]
        if rows == 0:
            raise ConfigurationError(f"'{filename}' has no data rows.")
        final = directory / f"{table}{TABLE_FILE_SUFFIX}"
        replaced = final.exists()
        os.replace(pending, final)
    finally:
        db.close()
        pending.unlink(missing_ok=True)
    return ImportedTable(name=table, rows=rows, columns=names, replaced=replaced)


def _literal(path: Path) -> str:
    return str(path).replace("'", "''")
