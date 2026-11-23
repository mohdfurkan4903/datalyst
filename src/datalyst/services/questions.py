from datalyst.core.exceptions import LLMError
from datalyst.core.models import AnalysisResult, DataSchema, QueryResult
from datalyst.metastore import Metastore
from datalyst.security import Principal
from datalyst.services.registry import AnalystRegistry


class QuestionService:
    def __init__(self, metastore: Metastore, registry: AnalystRegistry) -> None:
        self._metastore = metastore
        self._registry = registry

    def ask(
        self, principal: Principal, source_id: int, question: str
    ) -> AnalysisResult:
        principal.require_source(source_id)
        source = self._metastore.sources.get(source_id)
        analyst = self._registry.get(source)
        try:
            result = analyst.ask(question, self._view(principal, source_id))
        except LLMError as e:
            failed = AnalysisResult(question=question, error=str(e), source=source.name)
            failed.id = self._metastore.history.record(
                principal.user.id, source_id, failed
            )
            raise
        result.source = source.name
        result.id = self._metastore.history.record(principal.user.id, source_id, result)
        return result

    def run_sql(self, principal: Principal, source_id: int, sql: str) -> QueryResult:
        principal.require_source(source_id)
        source = self._metastore.sources.get(source_id)
        return self._registry.get(source).run_sql(sql, self._view(principal, source_id))

    def schema(self, principal: Principal, source_id: int) -> DataSchema:
        principal.require_source(source_id)
        source = self._metastore.sources.get(source_id)
        return self._registry.get(source).schema(view=self._view(principal, source_id))

    def _view(self, principal: Principal, source_id: int):
        def view(schema: DataSchema) -> DataSchema:
            return principal.visible_schema(source_id, schema)

        return view
