import json

from datalyst.core.exceptions import LLMError, NotFoundError
from datalyst.core.models import (
    AnalysisResult,
    DataSchema,
    LLMDataPolicy,
    QueryResult,
)
from datalyst.metastore import Metastore
from datalyst.security import Principal
from datalyst.services.configuration import ConfigService
from datalyst.services.memory import MemoryService
from datalyst.services.registry import AnalystRegistry

PREVIEW_ROWS = 3
PREVIEW_CHARS = 300


class QuestionService:
    def __init__(
        self,
        metastore: Metastore,
        registry: AnalystRegistry,
        config: ConfigService | None = None,
    ) -> None:
        self._metastore = metastore
        self._registry = registry
        self._config = config
        self.memory = MemoryService(metastore)

    def ask(
        self,
        principal: Principal,
        source_id: int,
        question: str,
        conversation_id: int | None = None,
    ) -> AnalysisResult:
        principal.require_source(source_id)
        source = self._metastore.sources.get(source_id)
        analyst = self._registry.get(source)
        settings = self._config.current() if self._config else None
        user_id = principal.user.id

        remembering = bool(settings and settings.memory_enabled)
        chat = (
            self._conversation(user_id, source_id, question, conversation_id)
            if remembering
            else None
        )
        notes: list[str] = []
        if remembering and settings.memory_learn:
            notes, only_instruction = self.memory.learn(
                user_id, source_id, analyst, question, settings.memory_max_items
            )
            if only_instruction:
                self._metastore.conversations.touch(chat)
                return AnalysisResult(
                    question=question,
                    source=source.name,
                    conversation_id=chat,
                    memory_notes=notes,
                )

        turns = (
            self._metastore.history.turns(chat, settings.memory_turns) if chat else []
        )
        memories = self.memory.for_prompt(user_id, source_id) if remembering else []
        try:
            result = analyst.ask(question, turns, memories)
        except LLMError:
            failed = AnalysisResult(
                question=question,
                error="The AI model could not answer this question.",
                source=source.name,
            )
            failed.id = self._metastore.history.record(user_id, source_id, failed, chat)
            raise
        result.source = source.name
        result.conversation_id = chat
        result.memory_notes = notes
        preview = (
            _preview(result.result)
            if result.success and source.llm_data_policy == LLMDataPolicy.ROWS
            else None
        )
        result.id = self._metastore.history.record(
            user_id, source_id, result, chat, preview
        )
        if chat:
            self._metastore.conversations.touch(chat)
        return result

    def run_sql(self, principal: Principal, source_id: int, sql: str) -> QueryResult:
        principal.require_source(source_id)
        source = self._metastore.sources.get(source_id)
        return self._registry.get(source).run_sql(sql)

    def schema(self, principal: Principal, source_id: int) -> DataSchema:
        principal.require_source(source_id)
        source = self._metastore.sources.get(source_id)
        return self._registry.get(source).schema()

    def _conversation(
        self, user_id: int, source_id: int, question: str, conversation_id: int | None
    ) -> int:
        chats = self._metastore.conversations
        if conversation_id is None:
            return chats.create(user_id, source_id, question).id
        chat = chats.get(conversation_id)
        if chat.user_id != user_id or chat.source_id != source_id:
            raise NotFoundError(f"Chat {conversation_id} does not exist.")
        return chat.id


def _preview(result: QueryResult | None) -> str | None:
    """The first few rows, compactly, for the assistant to refer back to."""
    if result is None or not result.rows:
        return None
    shown = result.model_dump(mode="json")["rows"][:PREVIEW_ROWS]
    text = f"columns {result.columns}: {json.dumps(shown, default=str)}"
    return text[:PREVIEW_CHARS]
