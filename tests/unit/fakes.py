from collections import defaultdict
from collections.abc import Callable

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from datalyst.connectors.postgres import PostgresQueryValidator
from datalyst.core.exceptions import QueryExecutionError
from datalyst.core.interfaces import DataSource
from datalyst.core.models import (
    Column,
    DataSchema,
    ForeignKey,
    QueryResult,
    SyncSummary,
    Table,
)


def fake_llm(*replies: str) -> GenericFakeChatModel:
    return GenericFakeChatModel(messages=iter(AIMessage(reply) for reply in replies))


def sql_reply(query: str) -> str:
    return f"```sql\n{query}\n```"


class FakeDataSource(DataSource):
    dialect = "PostgreSQL"
    query_language = "SQL"

    def __init__(
        self,
        execute: Callable[[str], QueryResult] | None = None,
        schema: DataSchema | None = None,
    ) -> None:
        self._schema = schema or DataSchema(
            tables=[
                Table(
                    schema_name="public",
                    name="orders",
                    columns=[Column(name="id", data_type="integer")],
                )
            ]
        )
        self._validator = PostgresQueryValidator()
        self._execute = execute or (
            lambda _: QueryResult(columns=["n"], rows=[[1]], row_count=1)
        )
        self.executed: list[str] = []
        self.schema_loads = 0

    def get_schema(self) -> DataSchema:
        self.schema_loads += 1
        return self._schema

    def validate(self, query: str) -> str:
        return self._validator.validate(query)

    def execute(self, query: str) -> QueryResult:
        self.executed.append(query)
        return self._execute(query)


def failing_on(*bad_queries: str) -> Callable[[str], QueryResult]:
    def execute(query: str) -> QueryResult:
        if query in bad_queries:
            raise QueryExecutionError(f'column "x" does not exist in: {query}')
        return QueryResult(columns=["n"], rows=[[1]], row_count=1)

    return execute


def _fk(column: str, table: str) -> ForeignKey:
    return ForeignKey(column=column, references_table=table, references_column="id")


def _columns(*specs: str) -> list[Column]:
    return [Column(name=name, data_type=kind) for name, kind in map(str.split, specs)]


def shop_tables() -> list[Table]:
    return [
        Table(
            schema_name="public",
            name="customers",
            description="People with a shop account",
            columns=_columns("id integer", "name text", "country text"),
            primary_key=["id"],
        ),
        Table(
            schema_name="public",
            name="products",
            columns=[
                *_columns("id integer", "name text", "price numeric"),
                Column(
                    name="category",
                    data_type="text",
                    sample_values=["Electronics", "Books"],
                ),
            ],
            primary_key=["id"],
        ),
        Table(
            schema_name="public",
            name="orders",
            columns=_columns("id integer", "customer_id integer", "status text"),
            primary_key=["id"],
            foreign_keys=[_fk("customer_id", "public.customers")],
        ),
        Table(
            schema_name="public",
            name="order_items",
            columns=_columns(
                "id integer",
                "order_id integer",
                "product_id integer",
                "quantity integer",
            ),
            primary_key=["id"],
            foreign_keys=[
                _fk("order_id", "public.orders"),
                _fk("product_id", "public.products"),
            ],
        ),
    ]


def noise_tables(count: int) -> list[Table]:
    return [
        Table(
            schema_name="ops",
            name=f"telemetry_batch_{i}",
            columns=_columns("id integer", "payload jsonb", f"sensor_{i} text"),
            primary_key=["id"],
        )
        for i in range(count)
    ]


def large_schema(noise: int = 60) -> DataSchema:
    return DataSchema(tables=[*noise_tables(noise), *shop_tables()])


class FakeCatalogStore:
    def __init__(self) -> None:
        self._schemas: dict[int, DataSchema] = {}
        self._versions: dict[int, int] = defaultdict(int)
        self.loads = 0

    def version(self, source_id: int) -> int:
        return self._versions[source_id]

    def load_schema(self, source_id: int) -> DataSchema:
        self.loads += 1
        return self._schemas[source_id]

    def store(self, source_id: int, schema: DataSchema) -> None:
        self._schemas[source_id] = schema
        self._versions[source_id] += 1

    def bump(self, source_id: int) -> None:
        self._versions[source_id] += 1


class FakeSynchronizer:
    def __init__(
        self, store: FakeCatalogStore, source_id: int, datasource: DataSource
    ) -> None:
        self._store = store
        self._source_id = source_id
        self._datasource = datasource

    def sync_now(self) -> SyncSummary:
        schema = self._datasource.get_schema()
        self._store.store(self._source_id, schema)
        return SyncSummary(tables=len(schema.tables))


class RecordingLLM:
    def __init__(self, *replies: str) -> None:
        self._replies = list(replies)
        self.prompts: list[str] = []

    def invoke(self, messages):
        self.prompts.append("\n".join(str(m.content) for m in messages))
        if not self._replies:
            raise RuntimeError("no more replies")
        return AIMessage(
            self._replies.pop(0),
            usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        )
