import json
import re
from collections.abc import Callable, Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from datalyst.core.models import (
    Column,
    ColumnProfile,
    ColumnStats,
    QueryResult,
    SemanticType,
    Table,
    ValueCount,
)

MATCH_RATIO = 0.9
CATEGORY_MAX_DISTINCT = 50
CATEGORY_MAX_RATIO = 0.2
VALUE_INDEX_MAX_DISTINCT = 1000
FREE_TEXT_MIN_LENGTH = 60
TOP_VALUES_KEPT = 25
EXAMPLES_KEPT = 5
EXAMPLE_MAX_LENGTH = 40
JSON_KEYS_KEPT = 15

TEXT_TYPES = (
    "text",
    "tinytext",
    "mediumtext",
    "longtext",
    "character varying",
    "character",
    "varchar",
    "char",
    "bpchar",
)
INTEGER_TYPES = ("smallint", "integer", "bigint", "int", "tinyint", "mediumint")
DECIMAL_TYPES = ("numeric", "decimal", "real", "double", "float", "money")
TYPE_MODIFIERS = ("unsigned", "zerofill")
ENUM_TYPES = ("enum", "set")
DATE_TYPES = ("date", "timestamp", "time")

BOOLEAN_PAIRS = [
    {"y", "n"},
    {"yes", "no"},
    {"t", "f"},
    {"true", "false"},
    {"0", "1"},
    {"on", "off"},
    {"active", "inactive"},
]

_PATTERNS: list[tuple[SemanticType, re.Pattern[str]]] = [
    (SemanticType.EMAIL, re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")),
    (SemanticType.URL, re.compile(r"^https?://\S+$", re.IGNORECASE)),
    (
        SemanticType.UUID,
        re.compile(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
            re.IGNORECASE,
        ),
    ),
    (SemanticType.IP_ADDRESS, re.compile(r"^(\d{1,3}\.){3}\d{1,3}$")),
]

_PHONE = re.compile(r"^\+?[\d\s().-]{7,20}$")

_NUMERIC = re.compile(r"^[-+]?\d+([.,]\d+)?$")
_ISO_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}")

_NAME_HINTS: list[tuple[SemanticType, frozenset[str]]] = [
    (SemanticType.MONEY_CENTS, frozenset({"cents", "cent", "paise", "pence"})),
    (SemanticType.PERCENT, frozenset({"pct", "percent", "percentage"})),
    (
        SemanticType.MONEY,
        frozenset(
            {
                "amt",
                "amount",
                "prc",
                "price",
                "cost",
                "sal",
                "salary",
                "revenue",
                "bdgt",
                "budget",
                "fee",
                "rfnd",
                "refund",
                "total",
                "tot",
                "paid",
                "balance",
                "spend",
                "spent",
            }
        ),
    ),
    (
        SemanticType.QUANTITY,
        frozenset({"qty", "quantity", "cnt", "count", "units", "pts", "points"}),
    ),
]
_COUNTRY_HINTS = frozenset({"ctry", "country", "cntry", "nation"})
_CURRENCY_HINTS = frozenset({"ccy", "currency", "cur", "curr"})
_KEY_SUFFIXES = frozenset(
    {"id", "cd", "code", "no", "num", "nbr", "key", "ref", "fk", "sk"}
)

_DATE_FORMATS: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"^\d{8}$"), "%Y%m%d", "YYYYMMDD"),
    (re.compile(r"^\d{4}-\d{2}-\d{2}$"), "%Y-%m-%d", "YYYY-MM-DD"),
    (re.compile(r"^\d{4}/\d{2}/\d{2}$"), "%Y/%m/%d", "YYYY/MM/DD"),
    (re.compile(r"^\d{2}/\d{2}/\d{4}$"), "%d/%m/%Y", "DD/MM/YYYY"),
    (re.compile(r"^\d{2}-\d{2}-\d{4}$"), "%d-%m-%Y", "DD-MM-YYYY"),
]


def name_tokens(name: str) -> list[str]:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    return re.findall(r"[a-z0-9]+", spaced.lower())


def type_family(data_type: str) -> str:
    base = data_type.split("(")[0].strip().lower()
    base = " ".join(w for w in base.split() if w not in TYPE_MODIFIERS)
    if base.startswith(TEXT_TYPES) or base in ENUM_TYPES:
        return "text"
    if base in INTEGER_TYPES:
        return "integer"
    if base.startswith(DECIMAL_TYPES):
        return "decimal"
    if base.startswith(DATE_TYPES):
        return "temporal"
    if base == "uuid":
        return "uuid"
    if base in ("json", "jsonb"):
        return "json"
    if base == "boolean":
        return "boolean"
    return "other"


def wants_value_index(column: Column, profile: ColumnProfile) -> bool:
    if type_family(column.data_type) not in ("text", "integer"):
        return False
    if profile.unique or profile.semantic_type in (
        SemanticType.EMAIL,
        SemanticType.URL,
        SemanticType.UUID,
        SemanticType.FREE_TEXT,
        SemanticType.PHONE,
        SemanticType.IDENTIFIER,
        SemanticType.MONEY,
        SemanticType.MONEY_CENTS,
    ):
        return False
    distinct = profile.distinct_count
    return distinct is not None and 0 < distinct <= VALUE_INDEX_MAX_DISTINCT


def profile_table(
    table: Table,
    stats: Sequence[ColumnStats],
    sample: QueryResult,
    top_values: dict[str, list[ValueCount]] | None = None,
) -> dict[str, ColumnProfile]:
    top_values = top_values or {}
    by_column = {s.column: s for s in stats}
    positions = {name: i for i, name in enumerate(sample.columns)}
    single_key = table.primary_key[0] if len(table.primary_key) == 1 else None
    rows = table.row_estimate
    profiles = {}
    for column in table.columns:
        index = positions.get(column.name)
        values = [row[index] for row in sample.rows] if index is not None else []
        profiles[column.name] = _profile_column(
            column,
            values,
            by_column.get(column.name),
            top_values.get(column.name, []),
            is_key=column.name == single_key,
            rows=rows,
        )
    return profiles


def _profile_column(
    column: Column,
    values: list[Any],
    stats: ColumnStats | None,
    top: list[ValueCount],
    *,
    is_key: bool,
    rows: int | None,
) -> ColumnProfile:
    present = [v for v in values if v is not None]
    texts = [_as_text(v) for v in present]
    family = type_family(column.data_type)
    sampled_whole_table = rows is not None and len(values) >= rows

    null_fraction = (
        stats.null_fraction
        if stats and stats.null_fraction is not None
        else (round(1 - len(present) / len(values), 4) if values else None)
    )
    distinct = stats.distinct if stats and stats.distinct is not None else None
    if distinct is None and texts:
        seen = len(set(texts))
        clearly_low = seen <= CATEGORY_MAX_DISTINCT and seen / len(texts) <= 0.1
        if sampled_whole_table or clearly_low:
            distinct = float(seen)
    unique = is_key or bool(
        rows and distinct is not None and distinct >= rows * 0.99 and rows > 1
    )
    if not top and stats and stats.common_values and rows:
        top = [
            ValueCount(value=value, count=max(1, round(freq * rows)))
            for value, freq in zip(
                stats.common_values, stats.common_frequencies, strict=False
            )
        ]

    semantic, value_format = _detect(column, family, texts, distinct, unique, rows)
    low, high = _range(family, semantic, value_format, present, stats)

    return ColumnProfile(
        sampled_rows=len(values),
        null_fraction=null_fraction,
        distinct_count=distinct,
        unique=unique,
        min_value=low,
        max_value=high,
        average_length=(
            round(sum(map(len, texts)) / len(texts), 1)
            if texts and family == "text"
            else None
        ),
        examples=_examples(texts),
        top_values=top[:TOP_VALUES_KEPT],
        semantic_type=semantic,
        format=value_format,
        json_keys=_json_keys(present) if family == "json" else [],
    )


def _detect(
    column: Column,
    family: str,
    texts: list[str],
    distinct: float | None,
    unique: bool,
    rows: int | None,
) -> tuple[SemanticType | None, str | None]:
    tokens = set(name_tokens(column.name))
    if family == "json":
        return SemanticType.JSON, None
    if family == "uuid":
        return SemanticType.UUID, None
    if family == "text" and texts:
        return _detect_text(tokens, texts, distinct, unique, rows)
    if family in ("integer", "decimal"):
        for semantic, hints in _NAME_HINTS:
            if tokens & hints:
                return semantic, None
        if family == "integer" and (unique or tokens & _KEY_SUFFIXES):
            return SemanticType.IDENTIFIER, None
        if (
            family == "integer"
            and distinct is not None
            and distinct <= 10
            and rows
            and rows > 20
        ):
            return SemanticType.CATEGORY, None
    return None, None


def _detect_text(
    tokens: set[str],
    texts: list[str],
    distinct: float | None,
    unique: bool,
    rows: int | None,
) -> tuple[SemanticType | None, str | None]:
    for semantic, pattern in _PATTERNS:
        if _share(texts, pattern.match) >= MATCH_RATIO:
            return semantic, None
    for pattern, parse_format, label in _DATE_FORMATS:
        if (
            _share(
                texts, lambda v, p=pattern, f=parse_format: p.match(v) and _parses(v, f)
            )
            >= MATCH_RATIO
        ):
            if label == "DD/MM/YYYY" and not any(int(v[:2]) > 12 for v in texts):
                label = "DD/MM/YYYY or MM/DD/YYYY"
            return SemanticType.DATE_TEXT, label
    if _share(texts, _ISO_DATETIME.match) >= MATCH_RATIO:
        return SemanticType.DATE_TEXT, "ISO 8601 timestamp"
    if _share(texts, _NUMERIC.match) >= MATCH_RATIO:
        return SemanticType.NUMERIC_TEXT, None
    if _share(texts, _PHONE.match) >= MATCH_RATIO:
        return SemanticType.PHONE, None

    lowered = {v.strip().lower() for v in texts}
    if len(lowered) == 2 and any(lowered == pair for pair in BOOLEAN_PAIRS):
        return SemanticType.BOOLEAN_FLAG, None
    if tokens & _COUNTRY_HINTS and all(len(v) == 2 and v.isalpha() for v in texts):
        return SemanticType.COUNTRY_CODE, None
    if tokens & _CURRENCY_HINTS and all(len(v) == 3 and v.isalpha() for v in texts):
        return SemanticType.CURRENCY_CODE, None
    average = sum(map(len, texts)) / len(texts)
    if average >= FREE_TEXT_MIN_LENGTH:
        return SemanticType.FREE_TEXT, None
    if unique:
        return SemanticType.IDENTIFIER if tokens & _KEY_SUFFIXES else None, None
    if (
        distinct is not None
        and distinct <= CATEGORY_MAX_DISTINCT
        and (not rows or distinct / rows <= CATEGORY_MAX_RATIO or rows <= 20)
    ):
        return SemanticType.CATEGORY, None
    return None, None


def _range(
    family: str,
    semantic: SemanticType | None,
    value_format: str | None,
    present: list[Any],
    stats: ColumnStats | None,
) -> tuple[str | None, str | None]:
    comparable = family in ("integer", "decimal", "temporal") or (
        semantic == SemanticType.DATE_TEXT
        and value_format in ("YYYYMMDD", "YYYY-MM-DD")
    )
    if not comparable or semantic == SemanticType.IDENTIFIER:
        return None, None
    if stats and len(stats.histogram_bounds) >= 2:
        return stats.histogram_bounds[0], stats.histogram_bounds[-1]
    if not present:
        return None, None
    try:
        return _as_text(min(present)), _as_text(max(present))
    except TypeError:
        return None, None


def _examples(texts: list[str]) -> list[str]:
    seen: list[str] = []
    for value in texts:
        short = (
            value
            if len(value) <= EXAMPLE_MAX_LENGTH
            else value[: EXAMPLE_MAX_LENGTH - 1] + "…"
        )
        if short not in seen:
            seen.append(short)
        if len(seen) == EXAMPLES_KEPT:
            break
    return seen


def _json_keys(values: list[Any]) -> list[str]:
    keys: dict[str, None] = {}
    for value in values:
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                continue
        if isinstance(value, dict):
            keys.update(dict.fromkeys(map(str, value)))
        if len(keys) >= JSON_KEYS_KEPT:
            break
    return list(keys)[:JSON_KEYS_KEPT]


def _share(texts: list[str], predicate: Callable[[str], Any]) -> float:
    return sum(1 for value in texts if predicate(value)) / len(texts) if texts else 0.0


def _parses(value: str, parse_format: str) -> bool:
    try:
        datetime.strptime(value, parse_format)
    except ValueError:
        return False
    return True


def _as_text(value: Any) -> str:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, dict | list):
        return json.dumps(value, default=str)
    return str(value)
