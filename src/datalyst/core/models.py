from typing import Any

from pydantic import BaseModel, Field


class Column(BaseModel):
    name: str
    data_type: str
    nullable: bool = True


class ForeignKey(BaseModel):
    column: str
    references_table: str
    references_column: str


class Table(BaseModel):
    schema_name: str
    name: str
    columns: list[Column] = Field(default_factory=list)
    primary_key: list[str] = Field(default_factory=list)
    foreign_keys: list[ForeignKey] = Field(default_factory=list)

    @property
    def qualified_name(self) -> str:
        return f"{self.schema_name}.{self.name}"


class DataSchema(BaseModel):
    tables: list[Table] = Field(default_factory=list)

    def get_table(self, qualified_name: str) -> Table | None:
        target = qualified_name.casefold()
        return next(
            (t for t in self.tables if t.qualified_name.casefold() == target), None
        )


class QueryResult(BaseModel):
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool = False
    elapsed_ms: float = 0.0


class AnalysisResult(BaseModel):
    question: str
    query: str | None = None
    result: QueryResult | None = None
    error: str | None = None
    attempts: int = 0
    trace_id: str | None = None
    duration_ms: float = 0.0

    @property
    def success(self) -> bool:
        return self.result is not None and self.error is None
