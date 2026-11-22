from datalyst.core.models import DataSchema, Table

REFUSAL_MARKER = "CANNOT_ANSWER:"

SYSTEM_PROMPT = """\
You are Datalyst, an expert data analyst. Translate the user's question into one \
{query_language} query for a {dialect} database.

Rules:
- Write exactly ONE read-only query. Never modify data or schema.
- Use only tables and columns from the schema. Never invent names.
- Qualify tables with their schema, e.g. public.orders.
- Join on the listed foreign keys (shown as "-> table.column").
- For row listings without an explicit size, add LIMIT {default_limit}.
- If the question asks to change data, reply exactly: {refusal} read-only access only
- If the schema cannot answer the question, reply exactly: {refusal} <short reason>

Reply with the query inside a single ```sql code block and nothing else."""

USER_PROMPT = """\
Schema:
{schema}

Question: {question}"""

REPAIR_PROMPT = """\
That query failed.

Error: {error}

Return a corrected query inside a single ```sql code block."""


def render_system_prompt(
    dialect: str, query_language: str, default_limit: int = 100
) -> str:
    return SYSTEM_PROMPT.format(
        dialect=dialect,
        query_language=query_language,
        default_limit=default_limit,
        refusal=REFUSAL_MARKER,
    )


def render_schema(schema: DataSchema) -> str:
    if not schema.tables:
        return "(no tables available)"
    return "\n".join(_render_table(table) for table in schema.tables)


def _render_table(table: Table) -> str:
    references = {fk.column: fk for fk in table.foreign_keys}
    columns = []
    for column in table.columns:
        text = f"{column.name} {column.data_type}"
        if column.name in table.primary_key:
            text += " PK"
        if fk := references.get(column.name):
            text += f" -> {fk.references_table}.{fk.references_column}"
        columns.append(text)
    return f"{table.qualified_name}({', '.join(columns)})"
