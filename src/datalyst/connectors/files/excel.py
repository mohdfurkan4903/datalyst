"""Read Excel workbooks (.xlsx, .xlsm) into tables.

Each sheet is written to a typed CSV file and then read back by DuckDB with
those exact types, so text such as the zip code ``02134`` stays text and
numbers and dates stay numbers and dates. Formulas are read as the value Excel
saved with them.
"""

import csv
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zipfile import BadZipFile

import duckdb
from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from datalyst.connectors.files.importer import (
    ImportedTable,
    column_names,
    literal,
    publish,
    table_name,
)
from datalyst.core.exceptions import ConfigurationError

HEADER_SEARCH_ROWS = 30
HEADER_FILL = 0.8
MAX_INTEGER = 2**63 - 1
ERROR_VALUES = frozenset(
    {"#N/A", "#DIV/0!", "#REF!", "#VALUE!", "#NAME?", "#NUM!", "#NULL!", "#SPILL!"}
)


@dataclass
class ColumnKinds:
    """What one column holds, learned in a first pass over the sheet."""

    kinds: set[type] = field(default_factory=set)
    only_midnight: bool = True
    too_big: bool = False

    def add(self, value: Any) -> None:
        self.kinds.add(type(value))
        if isinstance(value, datetime) and (value.time() != time(0) or value.tzinfo):
            self.only_midnight = False
        if isinstance(value, int) and not isinstance(value, bool):
            self.too_big = self.too_big or abs(value) > MAX_INTEGER

    @property
    def present(self) -> bool:
        return bool(self.kinds)

    def sql_type(self) -> str:
        kinds = self.kinds
        if kinds <= {bool}:
            return "BOOLEAN" if kinds else "VARCHAR"
        if kinds <= {int}:
            return "DOUBLE" if self.too_big else "BIGINT"
        if kinds <= {int, float}:
            return "DOUBLE"
        if kinds <= {datetime, date}:
            return "DATE" if self.only_midnight else "TIMESTAMP"
        if kinds <= {time}:
            return "TIME"
        return "VARCHAR"


def import_workbook(
    source: Path, filename: str, directory: Path
) -> list[ImportedTable]:
    try:
        workbook = load_workbook(source, read_only=True, data_only=True)
    except (InvalidFileException, BadZipFile, KeyError, OSError, ValueError) as e:
        raise ConfigurationError(
            f"Could not read '{filename}' as an Excel workbook. It may be damaged "
            "or protected with a password."
        ) from e

    try:
        sheets = [ws for ws in workbook.worksheets if ws.sheet_state == "visible"]
        found = [(ws.title, _read_sheet(ws)) for ws in sheets]
        found = [(title, data) for title, data in found if data is not None]
        if not found:
            raise ConfigurationError(f"'{filename}' has no data rows.")
        # One sheet is named after the file; several are named after the sheets.
        titles = [filename] if len(found) == 1 else [title for title, _ in found]
        names = _unique([table_name(t) for t in titles])
        imported = []
        with tempfile.TemporaryDirectory() as scratch:
            db = duckdb.connect(":memory:")
            try:
                for name, (title, data) in zip(names, found, strict=True):
                    imported.append(
                        _import_sheet(
                            db, data, name, filename, title, scratch, directory
                        )
                    )
            finally:
                db.close()
        return imported
    finally:
        workbook.close()


@dataclass
class Sheet:
    worksheet: Any
    header_row: int
    headers: list[str]
    types: list[str]
    rows: int


def _read_sheet(worksheet) -> Sheet | None:
    worksheet.reset_dimensions()  # some exporters write wrong sheet sizes
    header_row = _find_header(worksheet)
    if header_row is None:
        return None

    header_cells = next(
        worksheet.iter_rows(min_row=header_row, max_row=header_row, values_only=True)
    )
    columns: list[ColumnKinds] = []
    rows = 0
    for row in _data_rows(worksheet, header_row):
        rows += 1
        while len(columns) < len(row):
            columns.append(ColumnKinds())
        for index, value in enumerate(row):
            if value is not None:
                columns[index].add(value)
    if rows == 0:
        return None

    last_header = max(
        (i for i, cell in enumerate(header_cells) if _text(cell)), default=-1
    )
    last_data = max((i for i, c in enumerate(columns) if c.present), default=-1)
    width = max(last_header, last_data) + 1
    labels = [
        _text(header_cells[i]) if i < len(header_cells) else "" for i in range(width)
    ]
    types = [
        (columns[i].sql_type() if i < len(columns) else "VARCHAR") for i in range(width)
    ]
    return Sheet(worksheet, header_row, column_names(labels), types, rows)


def _import_sheet(
    db,
    sheet: Sheet,
    table: str,
    filename: str,
    title: str,
    scratch: str,
    directory: Path,
) -> ImportedTable:
    path = Path(scratch) / f"{table}.csv"
    with path.open("w", newline="", encoding="utf-8") as out:
        writer = csv.writer(out)
        writer.writerow(sheet.headers)
        for row in _data_rows(sheet.worksheet, sheet.header_row):
            cells = list(row[: len(sheet.types)])
            cells += [None] * (len(sheet.types) - len(cells))
            writer.writerow(
                [
                    _cell(value, kind)
                    for value, kind in zip(cells, sheet.types, strict=True)
                ]
            )
    columns = ", ".join(
        f"'{name}': '{kind}'"
        for name, kind in zip(sheet.headers, sheet.types, strict=True)
    )
    select = (
        f"SELECT * FROM read_csv('{literal(path)}', header = true, "
        f"columns = {{{columns}}}, nullstr = '', quote = '\"', escape = '\"')"
    )
    try:
        return publish(
            db, select, table, sheet.headers, f"{filename} ({title})", directory
        )
    except duckdb.Error as e:
        raise ConfigurationError(
            f"Could not read the sheet '{title}' in '{filename}': {e}"
        ) from e


def _find_header(worksheet) -> int | None:
    """The first row that looks like column titles: skips a title line or blank
    rows above the table."""
    rows = list(
        worksheet.iter_rows(min_row=1, max_row=HEADER_SEARCH_ROWS, values_only=True)
    )
    filled = [sum(1 for cell in row if _text(cell)) for row in rows]
    widest = max(filled, default=0)
    if widest == 0:
        return None
    return next(
        i + 1 for i, count in enumerate(filled) if count >= widest * HEADER_FILL
    )


def _data_rows(worksheet, header_row: int) -> Iterator[tuple]:
    for row in worksheet.iter_rows(min_row=header_row + 1, values_only=True):
        if any(_clean(cell) is not None for cell in row):
            yield tuple(_clean(cell) for cell in row)


def _clean(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip() if value.strip() else None
        return None if value in ERROR_VALUES else value
    if isinstance(value, timedelta):
        return _duration(value)
    return value


def _duration(value: timedelta) -> str:
    total = int(value.total_seconds())
    sign = "-" if total < 0 else ""
    hours, rest = divmod(abs(total), 3600)
    return f"{sign}{hours}:{rest // 60:02d}:{rest % 60:02d}"


def _text(value: Any) -> str:
    cleaned = _clean(value)
    return "" if cleaned is None else str(cleaned).strip()


def _cell(value: Any, kind: str) -> str:
    """One value as the text DuckDB will read back as ``kind``."""
    if value is None:
        return ""
    if kind == "BOOLEAN":
        return "true" if value else "false"
    if kind == "DATE":
        return (
            value.date().isoformat()
            if isinstance(value, datetime)
            else value.isoformat()
        )
    if kind == "TIMESTAMP":
        if isinstance(value, datetime):
            return value.replace(tzinfo=None).isoformat(sep=" ")
        return datetime.combine(value, time(0)).isoformat(sep=" ")
    if kind == "TIME":
        return value.isoformat()
    if kind in ("BIGINT", "DOUBLE"):
        return repr(float(value)) if kind == "DOUBLE" else str(value)
    return _as_text(value)


def _as_text(value: Any) -> str:
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else repr(value)
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date | time):
        return value.isoformat()
    return str(value)


def _unique(names: list[str]) -> list[str]:
    seen: set[str] = set()
    unique = []
    for name in names:
        candidate, count = name, 1
        while candidate in seen:
            count += 1
            candidate = f"{name}_{count}"
        seen.add(candidate)
        unique.append(candidate)
    return unique
