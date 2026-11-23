import re
from dataclasses import dataclass, field
from typing import Any

MAX_BAR_ROWS = 50
MAX_LINE_ROWS = 5000

_DATE_LIKE = re.compile(
    r"^(\d{4}-\d{2}(-\d{2})?([ T].*)?|\d{6}|\d{8}|\d{4}-W\d{2}|\d{4}-Q[1-4])$"
)
_YEAR = re.compile(r"^(19|20)\d{2}$")


@dataclass(frozen=True)
class ChartSpec:
    kind: str
    x: str | None = None
    y: list[str] = field(default_factory=list)
    color: str | None = None


def is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def numeric_columns(columns: list[str], rows: list[list[Any]]) -> list[str]:
    found = []
    for index, name in enumerate(columns):
        values = [row[index] for row in rows if row[index] is not None]
        if values and all(is_number(v) for v in values):
            found.append(name)
    return found


def looks_temporal(name: str, values: list[Any]) -> bool:
    texts = [str(v) for v in values if v is not None]
    if not texts:
        return False
    if all(_DATE_LIKE.match(t) for t in texts):
        return True
    lowered = name.lower()
    hinted = any(word in lowered for word in ("year", "month", "date", "day", "week"))
    return hinted and all(_YEAR.match(t) or _DATE_LIKE.match(t) for t in texts)


def choose_chart(columns: list[str], rows: list[list[Any]]) -> ChartSpec:
    if not rows or not columns:
        return ChartSpec("none")
    numbers = numeric_columns(columns, rows)
    if len(rows) == 1 and len(columns) == 1 and numbers:
        return ChartSpec("metric", y=numbers)
    labels = [c for c in columns if c not in numbers]
    measures = [c for c in numbers if not _identifier(c)]
    if not measures:
        return ChartSpec("none")
    if not labels:
        year_like = [c for c in numbers if looks_temporal(c, _values(columns, rows, c))]
        if year_like and len(numbers) > 1 and len(rows) > 1:
            x = year_like[0]
            return ChartSpec("line", x=x, y=[m for m in measures if m != x][:3])
        return ChartSpec("none")
    if len(rows) < 2:
        return ChartSpec("none")
    temporal = [c for c in labels if looks_temporal(c, _values(columns, rows, c))]
    if temporal and len(rows) <= MAX_LINE_ROWS:
        others = [c for c in labels if c != temporal[0]]
        color = others[0] if len(others) == 1 and len(measures) == 1 else None
        return ChartSpec("line", x=temporal[0], y=measures[:3], color=color)
    if len(rows) <= MAX_BAR_ROWS:
        color = labels[1] if len(labels) > 1 and len(measures) == 1 else None
        return ChartSpec("bar", x=labels[0], y=measures[:3], color=color)
    return ChartSpec("none")


def _identifier(name: str) -> bool:
    lowered = name.lower()
    return lowered == "id" or lowered.endswith(("_id", "_no", "_cd", "_code"))


def _values(columns: list[str], rows: list[list[Any]], name: str) -> list[Any]:
    index = columns.index(name)
    return [row[index] for row in rows]
