from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_serializer


class SemanticType(StrEnum):
    IDENTIFIER = "identifier"
    CATEGORY = "category"
    BOOLEAN_FLAG = "boolean_flag"
    EMAIL = "email"
    URL = "url"
    PHONE = "phone"
    UUID = "uuid"
    IP_ADDRESS = "ip_address"
    DATE_TEXT = "date_text"
    NUMERIC_TEXT = "numeric_text"
    MONEY = "money"
    MONEY_CENTS = "money_cents"
    PERCENT = "percent"
    QUANTITY = "quantity"
    COUNTRY_CODE = "country_code"
    CURRENCY_CODE = "currency_code"
    FREE_TEXT = "free_text"
    JSON = "json"


class ValueCount(BaseModel):
    value: str
    count: int


class ColumnProfile(BaseModel):
    sampled_rows: int = 0
    null_fraction: float | None = None
    distinct_count: float | None = None
    unique: bool = False
    min_value: str | None = None
    max_value: str | None = None
    average_length: float | None = None
    examples: list[str] = Field(default_factory=list)
    top_values: list[ValueCount] = Field(default_factory=list)
    semantic_type: SemanticType | None = None
    format: str | None = None
    json_keys: list[str] = Field(default_factory=list)


class Column(BaseModel):
    name: str
    data_type: str
    nullable: bool = True
    description: str | None = None
    sample_values: list[str] = Field(default_factory=list)
    synonyms: list[str] = Field(default_factory=list)
    profile: ColumnProfile | None = None


class ForeignKey(BaseModel):
    column: str
    references_table: str
    references_column: str


class RelationshipOrigin(StrEnum):
    DECLARED = "declared"
    INFERRED = "inferred"
    HUMAN = "human"


class InferredRelationship(BaseModel):
    child: "ColumnRef"
    parent: "ColumnRef"
    confidence: float
    evidence: dict[str, Any] = Field(default_factory=dict)


class TableKind(StrEnum):
    TABLE = "table"
    VIEW = "view"
    MATERIALIZED_VIEW = "materialized_view"


class TableRef(BaseModel, frozen=True):
    schema_name: str
    name: str

    @property
    def qualified_name(self) -> str:
        return f"{self.schema_name}.{self.name}"

    @classmethod
    def parse(cls, qualified_name: str) -> "TableRef":
        schema_name, _, name = qualified_name.partition(".")
        return cls(schema_name=schema_name, name=name)


class ColumnRef(BaseModel, frozen=True):
    table: TableRef
    column: str

    @property
    def qualified_name(self) -> str:
        return f"{self.table.qualified_name}.{self.column}"


class TableFingerprint(BaseModel):
    table: TableRef
    kind: TableKind
    fingerprint: str
    row_estimate: int | None = None


class ColumnStats(BaseModel):
    column: str
    null_fraction: float | None = None
    distinct: float | None = None
    common_values: list[str] = Field(default_factory=list)
    common_frequencies: list[float] = Field(default_factory=list)
    histogram_bounds: list[str] = Field(default_factory=list)
    average_width: int | None = None
    correlation: float | None = None


class Containment(BaseModel):
    sampled: int
    matched: int

    @property
    def ratio(self) -> float:
        return self.matched / self.sampled if self.sampled else 0.0


class Table(BaseModel):
    schema_name: str
    name: str
    kind: TableKind = TableKind.TABLE
    row_estimate: int | None = None
    description: str | None = None
    synonyms: list[str] = Field(default_factory=list)
    example_questions: list[str] = Field(default_factory=list)
    columns: list[Column] = Field(default_factory=list)
    primary_key: list[str] = Field(default_factory=list)
    foreign_keys: list[ForeignKey] = Field(default_factory=list)

    @property
    def qualified_name(self) -> str:
        return f"{self.schema_name}.{self.name}"

    @property
    def ref(self) -> TableRef:
        return TableRef(schema_name=self.schema_name, name=self.name)


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

    @field_serializer("rows", when_used="json")
    def _json_rows(self, rows: list[list[Any]]) -> list[list[Any]]:
        return [[_json_cell(value) for value in row] for row in rows]


def _json_cell(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, bytes | memoryview):
        return bytes(value).hex()
    if isinstance(value, timedelta):
        return value.total_seconds()
    return value


class AnalysisResult(BaseModel):
    id: int | None = None
    source: str | None = None
    question: str
    query: str | None = None
    result: QueryResult | None = None
    error: str | None = None
    attempts: int = 0
    tables: list[str] = Field(default_factory=list)
    rewritten_question: str | None = None
    trace_id: str | None = None
    duration_ms: float = 0.0

    @property
    def success(self) -> bool:
        return self.result is not None and self.error is None


class LLMDataPolicy(StrEnum):
    ROWS = "rows"
    STATS = "stats"
    NAMES = "names"


class SourceStatus(StrEnum):
    NEW = "new"
    READY = "ready"
    SYNCING = "syncing"
    ERROR = "error"


class DataSourceInfo(BaseModel):
    id: int
    name: str
    kind: str
    include_schemas: list[str] = Field(default_factory=list)
    llm_data_policy: LLMDataPolicy = LLMDataPolicy.ROWS
    open_to_all: bool = True
    sync_interval_seconds: int = 300
    catalog_version: int = 0
    status: SourceStatus = SourceStatus.NEW
    status_detail: str | None = None
    last_synced_at: datetime | None = None
    last_checked_at: datetime | None = None
    next_check_at: datetime | None = None
    created_at: datetime | None = None


class SyncSummary(BaseModel):
    tables: int = 0
    added: list[str] = Field(default_factory=list)
    altered: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.added or self.altered or self.removed)

    def merge(self, other: "SyncSummary") -> "SyncSummary":
        return SyncSummary(
            tables=self.tables + other.tables,
            added=sorted({*self.added, *other.added}),
            altered=sorted({*self.altered, *other.altered}),
            removed=sorted({*self.removed, *other.removed}),
        )


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class JobRequest(BaseModel):
    source_id: int
    kind: str
    target: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    priority: int = 100
    max_attempts: int = 5
    run_after: datetime | None = None
    delay: timedelta | None = None


class Job(BaseModel):
    id: int
    source_id: int
    kind: str
    target: str
    payload: dict[str, Any]
    priority: int
    status: JobStatus
    attempts: int
    max_attempts: int
    run_after: datetime
    locked_by: str | None = None
    error: str | None = None
    created_at: datetime | None = None
    finished_at: datetime | None = None


InferredRelationship.model_rebuild()


class ColumnDescription(BaseModel):
    description: str
    synonyms: list[str] = Field(default_factory=list)


class TableDescription(BaseModel):
    description: str
    synonyms: list[str] = Field(default_factory=list)
    example_questions: list[str] = Field(default_factory=list)
    columns: dict[str, ColumnDescription] = Field(default_factory=dict)


class LLMUsage(BaseModel):
    purpose: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    duration_ms: int | None = None


class GlossaryTerm(BaseModel):
    id: int | None = None
    term: str
    definition: str
    sql_hint: str | None = None
    created_by: str | None = None


class VerifiedQuery(BaseModel):
    id: int | None = None
    question: str
    sql: str
    tables: list[str] = Field(default_factory=list)
    status: str = "approved"
    created_by: str | None = None


class ValueHint(BaseModel):
    term: str
    table: str
    column: str
    value: str
    score: float

    @property
    def text(self) -> str:
        literal = self.value.replace("'", "''")
        return f"\"{self.term}\" matches {self.table}.{self.column} = '{literal}'"


class Role(StrEnum):
    ADMIN = "admin"
    ANALYST = "analyst"


class User(BaseModel):
    id: int
    email: str
    name: str = ""
    role: Role = Role.ANALYST
    active: bool = True
    token_version: int = 0
    created_at: datetime | None = None
    last_login_at: datetime | None = None


class HistoryEntry(BaseModel):
    id: int
    user_id: int | None = None
    source_id: int | None = None
    question: str
    rewritten_question: str | None = None
    tables: list[str] = Field(default_factory=list)
    sql: str | None = None
    status: str
    error: str | None = None
    attempts: int = 0
    row_count: int | None = None
    duration_ms: int | None = None
    feedback: int | None = None
    feedback_note: str | None = None
    created_at: datetime | None = None
