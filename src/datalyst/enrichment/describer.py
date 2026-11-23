import hashlib
import json
import re
import time
from collections.abc import Sequence
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from pydantic import ValidationError

from datalyst.core.exceptions import LLMError
from datalyst.core.models import (
    Column,
    ColumnDescription,
    LLMDataPolicy,
    LLMUsage,
    QueryResult,
    Table,
    TableDescription,
)
from datalyst.llm.messages import message_text

PROMPT_VERSION = 2
COLUMNS_PER_CALL = 80
CELL_MAX_LENGTH = 40
VALUES_SHOWN = 10
DESCRIPTION_MAX_LENGTH = 400
SYNONYMS_KEPT = 8
QUESTIONS_KEPT = 3

SYSTEM_PROMPT = """\
You document database tables for business analysts who will ask questions in plain \
English. Using only the evidence given, write:
- for the table: one or two sentences saying what one row represents and what the \
table is used for;
- for every listed column: a short description of what it means, including units and \
encodings (for example "amount in cents", "date stored as text in YYYYMMDD format");
- for columns holding short codes, the likely meaning of each code, using the table \
name, related tables and how often each code appears (for example "status: O likely \
open, P likely paid, S likely shipped, X likely cancelled");
- synonyms business users might use for the table and for each column;
- up to three example questions an analyst would ask, about totals, trends, rankings \
or comparisons, not about one specific id.

Say "likely" when a meaning is an educated guess. Describe every listed column and \
do not invent columns. \
Reply with one JSON object and nothing else, in exactly this shape:
{"description": "...", "synonyms": ["..."], "example_questions": ["..."],
 "columns": {"<column name>": {"description": "...", "synonyms": ["..."]}}}"""

MISSING_PROMPT = """

Your previous answer skipped these columns: {columns}. Describe them now, in the \
same JSON shape."""

RETRY_PROMPT = """\
That reply could not be used: {error}
Reply again with only the JSON object in the requested shape."""

_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


def description_key(
    table: Table, incoming: Sequence[str], policy: LLMDataPolicy, model: str
) -> str:
    parts: list[Any] = [
        PROMPT_VERSION,
        table.qualified_name,
        table.kind,
        table.description,
        policy,
        model,
        sorted(incoming),
        sorted(
            (fk.column, fk.references_table, fk.references_column)
            for fk in table.foreign_keys
        ),
        table.primary_key,
    ]
    for column in table.columns:
        profile = column.profile
        parts.append(
            [
                column.name,
                column.data_type,
                column.description,
                profile.semantic_type if profile else None,
                profile.format if profile else None,
                sorted(column.sample_values) if policy != LLMDataPolicy.NAMES else None,
            ]
        )
    return hashlib.sha256(json.dumps(parts, default=str).encode()).hexdigest()


def render_evidence(
    table: Table,
    columns: Sequence[Column],
    incoming: Sequence[str],
    policy: LLMDataPolicy,
    sample: QueryResult | None = None,
) -> str:
    size = (
        f", about {table.row_estimate} rows" if table.row_estimate is not None else ""
    )
    lines = [
        f"Table: {table.qualified_name} ({table.kind.value.replace('_', ' ')}{size})"
    ]
    if table.description:
        lines.append(f"Existing comment: {table.description}")
    joins = {
        fk.column: f"{fk.references_table}.{fk.references_column}"
        for fk in table.foreign_keys
    }
    lines.append("Columns:")
    for column in columns:
        lines.append(
            f"- {_render_column(table, column, joins.get(column.name), policy)}"
        )
    if incoming:
        lines.append("Referenced by: " + ", ".join(sorted(incoming)))
    if policy == LLMDataPolicy.ROWS and sample and sample.rows:
        wanted = [c.name for c in columns]
        positions = [sample.columns.index(n) for n in wanted if n in sample.columns]
        lines.append(f"Sample rows ({len(sample.rows)}):")
        lines.append(" | ".join(sample.columns[i] for i in positions))
        for row in sample.rows:
            lines.append(" | ".join(_cell(row[i]) for i in positions))
    return "\n".join(lines)


def describe(
    llm: BaseChatModel,
    table: Table,
    incoming: Sequence[str],
    policy: LLMDataPolicy,
    sample: QueryResult | None = None,
    model_name: str = "",
) -> tuple[TableDescription, list[LLMUsage]]:
    merged: TableDescription | None = None
    usage: list[LLMUsage] = []
    for start in range(0, max(len(table.columns), 1), COLUMNS_PER_CALL):
        columns = table.columns[start : start + COLUMNS_PER_CALL]
        evidence = render_evidence(table, columns, incoming, policy, sample)
        names = [c.name for c in columns]
        part, calls = _ask(llm, evidence, names, model_name)
        usage.extend(calls)
        if missing := [n for n in names if n not in part.columns]:
            retry_evidence = evidence + MISSING_PROMPT.format(
                columns=", ".join(missing)
            )
            extra, calls = _ask(llm, retry_evidence, missing, model_name)
            usage.extend(calls)
            part.columns.update(extra.columns)
        if merged is None:
            merged = part
        else:
            merged.columns.update(part.columns)
    assert merged is not None
    return merged, usage


def parse_description(text: str, columns: Sequence[str]) -> TableDescription:
    match = _JSON_OBJECT.search(text)
    if match is None:
        raise ValueError("no JSON object found")
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as e:
        raise ValueError(f"invalid JSON ({e})") from e
    data = data if isinstance(data, dict) else {}
    try:
        parsed = TableDescription.model_validate(
            {**data, "columns": _known_columns(data.get("columns"), columns)}
        )
    except ValidationError as e:
        raise ValueError(f"unexpected shape ({e.error_count()} problems)") from e
    if not parsed.description.strip():
        raise ValueError("the table description is empty")
    return _tidy(parsed)


def _ask(
    llm: BaseChatModel, evidence: str, columns: list[str], model_name: str
) -> tuple[TableDescription, list[LLMUsage]]:
    messages: list[BaseMessage] = [SystemMessage(SYSTEM_PROMPT), HumanMessage(evidence)]
    usage: list[LLMUsage] = []
    for attempt in range(2):
        started = time.perf_counter()
        try:
            response = llm.invoke(messages)
        except Exception as e:
            raise LLMError(f"Describing failed: {e}") from e
        usage.append(_usage(response, model_name, started))
        text = message_text(response)
        try:
            return parse_description(text, columns), usage
        except ValueError as e:
            if attempt == 1:
                raise LLMError(f"Unusable description from the model: {e}") from e
            messages += [AIMessage(text), HumanMessage(RETRY_PROMPT.format(error=e))]
    raise AssertionError("unreachable")


def _known_columns(raw: Any, columns: Sequence[str]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    wanted = {name.lower(): name for name in columns}
    known = {}
    for name, value in raw.items():
        target = wanted.get(str(name).lower())
        if target is None:
            continue
        if isinstance(value, str):
            value = {"description": value}
        if isinstance(value, dict) and str(value.get("description", "")).strip():
            known[target] = value
    return known


def _tidy(description: TableDescription) -> TableDescription:
    return TableDescription(
        description=_clip(description.description),
        synonyms=_short_list(description.synonyms, SYNONYMS_KEPT),
        example_questions=_short_list(
            description.example_questions, QUESTIONS_KEPT, 200
        ),
        columns={
            name: ColumnDescription(
                description=_clip(column.description),
                synonyms=_short_list(column.synonyms, SYNONYMS_KEPT),
            )
            for name, column in description.columns.items()
        },
    )


def _render_column(
    table: Table, column: Column, join: str | None, policy: LLMDataPolicy
) -> str:
    parts = [f"{column.name} {column.data_type}"]
    if column.name in table.primary_key:
        parts.append("primary key")
    if join:
        parts.append(f"joins {join}")
    if column.description:
        parts.append(f"comment: {column.description}")
    profile = column.profile
    if profile:
        if profile.semantic_type:
            meaning = profile.semantic_type.value.replace("_", " ")
            parts.append(f"{meaning} {profile.format}" if profile.format else meaning)
        if profile.unique and column.name not in table.primary_key:
            parts.append("unique")
        if policy != LLMDataPolicy.NAMES:
            if profile.null_fraction:
                parts.append(f"{profile.null_fraction:.0%} empty")
            if profile.distinct_count is not None:
                parts.append(f"{profile.distinct_count:.0f} distinct")
            if profile.min_value is not None:
                parts.append(f"range {profile.min_value}..{profile.max_value}")
            if profile.top_values:
                total = sum(v.count for v in profile.top_values) or 1
                shown = ", ".join(
                    f"{_cell(v.value)} ({v.count / total:.0%})"
                    for v in profile.top_values[:VALUES_SHOWN]
                )
                parts.append(f"values: {shown}")
            elif profile.examples:
                parts.append("examples: " + ", ".join(map(_cell, profile.examples)))
            if profile.json_keys:
                parts.append("JSON keys: " + ", ".join(profile.json_keys))
    return ", ".join(parts)


def _usage(response: Any, model_name: str, started: float) -> LLMUsage:
    metadata = getattr(response, "usage_metadata", None) or {}
    return LLMUsage(
        purpose="describe_table",
        model=model_name,
        input_tokens=metadata.get("input_tokens"),
        output_tokens=metadata.get("output_tokens"),
        duration_ms=round((time.perf_counter() - started) * 1000),
    )


def _cell(value: Any) -> str:
    text = "NULL" if value is None else str(value).replace("\n", " ")
    return text if len(text) <= CELL_MAX_LENGTH else text[: CELL_MAX_LENGTH - 1] + "…"


def _clip(text: str, limit: int = DESCRIPTION_MAX_LENGTH) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _short_list(values: list[str], limit: int, length: int = 40) -> list[str]:
    seen: dict[str, None] = {}
    for value in values:
        cleaned = " ".join(str(value).split())
        if cleaned and len(cleaned) <= length:
            seen.setdefault(cleaned)
    return list(seen)[:limit]
