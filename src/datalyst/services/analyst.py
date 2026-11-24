import time
from collections.abc import Sequence

from datalyst.agent import AnalystAgent
from datalyst.agent.memory import MemoryUpdate
from datalyst.catalog import SchemaCatalog
from datalyst.catalog.sqlrefs import catalog_problems
from datalyst.core.exceptions import UnsafeQueryError, brief
from datalyst.core.interfaces import DataSource
from datalyst.core.models import (
    AnalysisResult,
    DataSchema,
    Memory,
    QueryResult,
    Turn,
)


class AnalystService:
    def __init__(
        self, datasource: DataSource, catalog: SchemaCatalog, agent: AnalystAgent
    ) -> None:
        self._datasource = datasource
        self._catalog = catalog
        self._agent = agent

    @property
    def sql_dialect(self) -> str:
        return self._datasource.sql_dialect

    @property
    def default_schema(self) -> str:
        return self._datasource.default_schema

    def schema(self, refresh: bool = False) -> DataSchema:
        return self._catalog.get(refresh=refresh)

    def ask(
        self,
        question: str,
        turns: Sequence[Turn] = (),
        memories: Sequence[str] = (),
    ) -> AnalysisResult:
        start = time.perf_counter()
        state = self._agent.run(question, self._catalog.get(), turns, memories)
        return AnalysisResult(
            question=question,
            query=state.get("query"),
            result=state.get("result"),
            error=brief(state["error"]) if state.get("error") else None,
            attempts=state.get("attempts", 0),
            tables=state.get("tables", []),
            rewritten_question=state.get("rewritten_question"),
            duration_ms=round((time.perf_counter() - start) * 1000, 2),
        )

    def extract_memory(self, question: str, existing: Sequence[Memory]) -> MemoryUpdate:
        return self._agent.extract_memory(question, existing)

    def run_sql(self, sql: str) -> QueryResult:
        query = self._datasource.validate(sql)
        if problems := catalog_problems(
            query,
            self._catalog.get(),
            self._datasource.sql_dialect,
            self._datasource.default_schema,
        ):
            raise UnsafeQueryError(" ".join(problems))
        return self._datasource.execute(query)

    def close(self) -> None:
        self._datasource.close()
