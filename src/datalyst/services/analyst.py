import threading
import time

from datalyst.agent import AnalystAgent
from datalyst.agent.prompts import render_schema
from datalyst.core.interfaces import DataSource
from datalyst.core.models import AnalysisResult, DataSchema


class AnalystService:
    def __init__(self, datasource: DataSource, agent: AnalystAgent) -> None:
        self._datasource = datasource
        self._agent = agent
        self._schema: DataSchema | None = None
        self._schema_lock = threading.Lock()

    def schema(self, refresh: bool = False) -> DataSchema:
        with self._schema_lock:
            if self._schema is None or refresh:
                self._schema = self._datasource.get_schema()
            return self._schema

    def ask(self, question: str) -> AnalysisResult:
        start = time.perf_counter()
        state = self._agent.run(question, render_schema(self.schema()))
        return AnalysisResult(
            question=question,
            query=state.get("query"),
            result=state.get("result"),
            error=state.get("error"),
            attempts=state.get("attempts", 0),
            duration_ms=round((time.perf_counter() - start) * 1000, 2),
        )

    def close(self) -> None:
        self._datasource.close()
