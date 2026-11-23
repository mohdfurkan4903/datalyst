import hashlib
from collections.abc import Mapping
from dataclasses import dataclass

from datalyst.core.models import Column, DataSchema, Table

TABLE = "table"
COLUMN = "column"
QUERY = "query"


@dataclass(frozen=True)
class EmbeddingDocument:
    object_type: str
    object_id: int
    text: str

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.text.encode()).hexdigest()


def table_document(table: Table) -> str:
    lines = [f"Table {table.qualified_name}"]
    if table.description:
        lines.append(table.description)
    if table.synonyms:
        lines.append("Also called: " + ", ".join(table.synonyms))
    lines.append("Columns: " + ", ".join(_column_label(c) for c in table.columns))
    if table.example_questions:
        lines.append("Questions: " + " ".join(table.example_questions))
    return "\n".join(lines)


def column_document(table: Table, column: Column) -> str:
    lines = [f"Column {table.qualified_name}.{column.name} ({column.data_type})"]
    if column.description:
        lines.append(column.description)
    if column.synonyms:
        lines.append("Also called: " + ", ".join(column.synonyms))
    if column.profile and column.profile.semantic_type:
        lines.append(f"Kind: {column.profile.semantic_type.value.replace('_', ' ')}")
    if column.sample_values:
        lines.append("Values: " + ", ".join(column.sample_values[:15]))
    return "\n".join(lines)


def build_documents(
    schema: DataSchema,
    ids: Mapping[tuple[str, str, str | None], int],
    include_columns: bool = True,
) -> list[EmbeddingDocument]:
    documents = []
    for table in schema.tables:
        table_id = ids.get((table.schema_name, table.name, None))
        if table_id is not None:
            documents.append(EmbeddingDocument(TABLE, table_id, table_document(table)))
        if not include_columns:
            continue
        for column in table.columns:
            column_id = ids.get((table.schema_name, table.name, column.name))
            if column_id is not None:
                documents.append(
                    EmbeddingDocument(COLUMN, column_id, column_document(table, column))
                )
    return documents


def vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{value:.7g}" for value in vector) + "]"


def _column_label(column: Column) -> str:
    if column.description:
        return f"{column.name} ({column.description})"
    return column.name
