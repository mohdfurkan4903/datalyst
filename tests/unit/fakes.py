from collections.abc import Callable

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from datalyst.connectors.postgres import PostgresQueryValidator
from datalyst.core.exceptions import QueryExecutionError
from datalyst.core.interfaces import DataSource
from datalyst.core.models import Column, DataSchema, QueryResult, Table


def fake_llm(*replies: str) -> GenericFakeChatModel:
    return GenericFakeChatModel(messages=iter(AIMessage(reply) for reply in replies))


def sql_reply(query: str) -> str:
    return f"```sql\n{query}\n```"


class FakeDataSource(DataSource):
    dialect = "PostgreSQL"
    query_language = "SQL"

    def __init__(self, execute: Callable[[str], QueryResult] | None = None) -> None:
        self._validator = PostgresQueryValidator()
        self._execute = execute or (
            lambda _: QueryResult(columns=["n"], rows=[[1]], row_count=1)
        )
        self.executed: list[str] = []
        self.schema_loads = 0

    def get_schema(self) -> DataSchema:
        self.schema_loads += 1
        return DataSchema(
            tables=[
                Table(
                    schema_name="public",
                    name="orders",
                    columns=[Column(name="id", data_type="integer")],
                )
            ]
        )

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
