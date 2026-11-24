import logging

from datalyst.agent.memory import mentions_memory
from datalyst.core.exceptions import LLMError
from datalyst.core.models import Memory, MemoryOrigin
from datalyst.metastore import Metastore
from datalyst.services.analyst import AnalystService

logger = logging.getLogger(__name__)

FULL = "Memory is full. Remove something on the Memory page to save more."


class MemoryService:
    """Long-term memory: what the assistant keeps about each user across chats."""

    def __init__(self, metastore: Metastore) -> None:
        self._memories = metastore.memories

    def for_prompt(self, user_id: int, source_id: int) -> list[str]:
        return [m.text for m in self._memories.list(user_id, source_id)]

    def save(
        self,
        user_id: int,
        text: str,
        source_id: int | None,
        origin: MemoryOrigin,
        max_items: int,
    ) -> Memory | None:
        """Save one memory, making room by dropping the oldest learned one.
        Returns None when memory is full of things the user saved themselves."""
        if self._memories.count(user_id) >= max_items and not (
            self._memories.drop_oldest_learned(user_id)
        ):
            return None
        return self._memories.add(user_id, text, source_id, origin)

    def learn(
        self,
        user_id: int,
        source_id: int,
        analyst: AnalystService,
        question: str,
        max_items: int,
    ) -> tuple[list[str], bool]:
        """Save what the message asks to remember. Returns what was done, and
        whether the message was only an instruction with nothing to look up."""
        if not mentions_memory(question):
            return [], False
        existing = self._memories.list(user_id, source_id)
        try:
            update = analyst.extract_memory(question, existing)
        except LLMError:
            logger.warning("Could not check a message for memories", exc_info=True)
            return [], False

        notes = []
        by_id = {m.id: m for m in existing}
        for number in update.forget:
            self._memories.delete(user_id, number)
            notes.append(f"Forgot: {by_id[number].text}")
        for new in update.remember:
            scope = None if new.all_sources else source_id
            if self.save(user_id, new.text, scope, MemoryOrigin.AGENT, max_items):
                notes.append(f"Remembered: {new.text}")
            else:
                notes.append(FULL)
                break
        return notes, update.only_instruction and bool(notes)
