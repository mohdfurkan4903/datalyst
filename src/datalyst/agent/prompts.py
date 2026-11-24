import re
from collections.abc import Sequence

from datalyst.core.models import (
    Column,
    DataSchema,
    GlossaryTerm,
    SemanticType,
    Table,
    ValueHint,
    VerifiedQuery,
)

REFUSAL_MARKER = "CANNOT_ANSWER:"
MAX_NOTE_LENGTH = 300
SELECT_NONE = "NONE"

SYSTEM_PROMPT = """\
You are Datalyst, an expert data analyst. Translate the user's question into one \
{query_language} query for a {dialect} database.

Rules:
- Write exactly ONE read-only query. Never modify data or schema.
- Use only tables and columns from the schema. Never invent names.
- Qualify tables with their schema, e.g. {default_schema}.orders.
- Join on the listed foreign keys (shown as "-> table.column").
- Read the notes after "--": they explain meanings, codes and units.
- Use the listed values exactly as written when filtering on a column.
- Columns marked "text date" hold dates as text: convert them with \
{text_date_function} before comparing, sorting or grouping by date.
- Columns marked "cents" hold money in cents: divide by 100.0 for currency amounts.
- Follow the definitions under "Business terms" when the question uses those terms.
- "Verified examples" are trusted queries for this database: reuse their patterns.
- "Conversation so far" lists the earlier questions of this chat with their queries. \
When the question is a follow-up ("and for Germany?", "same but by month", "who are \
they?"), build on the earlier query instead of starting over.
- "About this user" lists definitions and preferences the user saved earlier. Follow \
them unless the question says otherwise.
- For row listings without an explicit size, add LIMIT {default_limit}.
- If the question asks to change data, reply exactly: {refusal} read-only access only
- If the schema cannot answer the question, reply exactly: {refusal} <short reason>

Reply with the query inside a single ```sql code block and nothing else."""

USER_PROMPT = """\
Schema:
{schema}
{context}
Question: {question}"""

REPAIR_PROMPT = """\
That query failed.

Error: {error}

Return a corrected query inside a single ```sql code block."""

HEALED_PROMPT = """\
That query failed because the database schema changed.

Error: {error}

The current definition of the tables involved:
{schema}

Return a corrected query inside a single ```sql code block."""

REWRITE_PROMPT = """\
You prepare questions for a {dialect} database that has many tables. \
Do not write a query.

1. Rewrite the question as one clear, standalone analytics question. \
Keep its meaning; do not add filters or assumptions. If it refers to the earlier \
conversation ("it", "those", "same", "and for 2023"), use the conversation to \
say what it refers to.
2. List search terms that help find the relevant tables: the entities, metrics, \
filters and values in the question, likely table and column names, and common \
synonyms (for example revenue: sales, amount, price; client: customer, account).

Reply in exactly this format:
QUESTION: <rewritten question>
TERMS: <comma-separated terms>"""

SELECT_PROMPT = """\
You pick the tables needed to answer a question about a {dialect} database. \
Choose from the candidates only. Include lookup tables needed to decode codes or \
names, and tables needed to join the others. Reply with the needed table names, \
one per line, most important first, and nothing else. Reply {none} if no \
candidate can answer the question."""

_SELECTED_LINE = re.compile(r"^[\s\-*\d.)]*`?([\w.\"]+)`?")


def render_system_prompt(
    dialect: str,
    query_language: str,
    default_limit: int = 100,
    default_schema: str = "public",
    text_date_function: str = "to_date(column, 'FORMAT')",
) -> str:
    return SYSTEM_PROMPT.format(
        dialect=dialect,
        query_language=query_language,
        default_limit=default_limit,
        default_schema=default_schema,
        text_date_function=text_date_function,
        refusal=REFUSAL_MARKER,
    )


def render_rewrite_prompt(dialect: str) -> str:
    return REWRITE_PROMPT.format(dialect=dialect)


def render_select_prompt(dialect: str) -> str:
    return SELECT_PROMPT.format(dialect=dialect, none=SELECT_NONE)


def render_candidates(
    schema: DataSchema, names: Sequence[str], hints: Sequence[ValueHint] = ()
) -> str:
    by_name = {t.qualified_name: t for t in schema.tables}
    lines = []
    for name in names:
        table = by_name.get(name)
        if table is None:
            continue
        about = f": {_one_line(table.description)}" if table.description else ""
        columns = ", ".join(c.name for c in table.columns)
        lines.append(f"- {name}{about} | columns: {columns}")
    if hints:
        lines.append("Matching values: " + "; ".join(h.text for h in hints))
    return "\n".join(lines)


def parse_selection(text: str, candidates: Sequence[str]) -> list[str]:
    by_full = {name.lower(): name for name in candidates}
    by_bare: dict[str, list[str]] = {}
    for name in candidates:
        by_bare.setdefault(name.split(".")[-1].lower(), []).append(name)
    picked: list[str] = []
    for line in text.splitlines():
        match = _SELECTED_LINE.match(line)
        if not match:
            continue
        token = match.group(1).replace('"', "").lower().rstrip(".")
        name = by_full.get(token) or (
            by_bare[token][0] if len(by_bare.get(token, [])) == 1 else None
        )
        if name and name not in picked:
            picked.append(name)
    return picked


def render_context(
    glossary: Sequence[GlossaryTerm] = (),
    hints: Sequence[ValueHint] = (),
    examples: Sequence[VerifiedQuery] = (),
) -> str:
    sections = []
    if glossary:
        lines = ["Business terms:"]
        for term in glossary:
            sql = f" (SQL: {_one_line(term.sql_hint)})" if term.sql_hint else ""
            lines.append(f"- {term.term}: {_one_line(term.definition)}{sql}")
        sections.append("\n".join(lines))
    if hints:
        sections.append(
            "\n".join(
                ["Matching values found in the data:", *(f"- {h.text}" for h in hints)]
            )
        )
    if examples:
        lines = ["Verified examples:"]
        for example in examples:
            lines.append(f"Q: {example.question}\n```sql\n{example.sql.strip()}\n```")
        sections.append("\n".join(lines))
    return "".join(f"\n{section}\n" for section in sections)


def render_schema(schema: DataSchema) -> str:
    if not schema.tables:
        return "(no tables available)"
    return "\n\n".join(_render_table(table) for table in schema.tables)


def _render_table(table: Table) -> str:
    references = {fk.column: fk for fk in table.foreign_keys}
    lines = [_with_note(table.qualified_name, table.description)]
    for column in table.columns:
        text = f"  {column.name} {column.data_type}"
        if column.name in table.primary_key:
            text += " PK"
        if fk := references.get(column.name):
            text += f" -> {fk.references_table}.{fk.references_column}"
        if hint := _semantic_hint(column):
            text += f" [{hint}]"
        if column.sample_values:
            text += f" (values: {_quote_values(column)})"
        lines.append(_with_note(text, column.description))
    return "\n".join(lines)


def _semantic_hint(column: Column) -> str | None:
    profile = column.profile
    if profile is None:
        return None
    if column.data_type.startswith(("date", "timestamp")):
        return _with_range(None, profile.min_value, profile.max_value)
    kind = profile.semantic_type
    if kind == SemanticType.DATE_TEXT:
        label = f"text date {profile.format}" if profile.format else "text date"
        return _with_range(label, profile.min_value, profile.max_value)
    if kind == SemanticType.MONEY_CENTS:
        return "money in cents"
    if kind == SemanticType.NUMERIC_TEXT:
        return "number stored as text"
    if kind == SemanticType.JSON and profile.json_keys:
        return "json keys: " + ", ".join(profile.json_keys[:8])
    return None


def _with_range(label: str | None, low: str | None, high: str | None) -> str | None:
    if low is None or high is None:
        return label
    span = f"{low[:10]}..{high[:10]}"
    return f"{label}, {span}" if label else span


def _quote_values(column: Column) -> str:
    return ", ".join(
        "'" + value.replace("'", "''") + "'" for value in column.sample_values
    )


def _with_note(text: str, note: str | None) -> str:
    return f"{text} -- {_one_line(note)}" if note else text


def _one_line(note: str | None) -> str:
    note = " ".join((note or "").split())
    if len(note) > MAX_NOTE_LENGTH:
        note = note[: MAX_NOTE_LENGTH - 3] + "..."
    return note
