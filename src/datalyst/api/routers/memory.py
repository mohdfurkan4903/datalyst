from fastapi import APIRouter

from datalyst.api.deps import CurrentUser, Services
from datalyst.api.schemas import MemoryCreate, MemoryUpdate
from datalyst.core.exceptions import ConfigurationError, NotFoundError
from datalyst.core.models import Conversation, Memory, MemoryOrigin

router = APIRouter(tags=["memory"])


@router.get("/api/conversations")
def list_conversations(
    user: CurrentUser, app: Services, source_id: int | None = None, limit: int = 50
) -> list[Conversation]:
    """The signed-in user's own chats."""
    return app.metastore.conversations.list(
        user.user.id, source_id, limit=min(limit, 200)
    )


@router.get("/api/conversations/{conversation_id}")
def get_conversation(conversation_id: int, user: CurrentUser, app: Services) -> dict:
    chat = _own_chat(app, user, conversation_id)
    return {
        "conversation": chat,
        "entries": app.metastore.history.conversation(chat.id),
    }


@router.delete("/api/conversations/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: int, user: CurrentUser, app: Services) -> None:
    app.metastore.conversations.delete(_own_chat(app, user, conversation_id).id)


@router.get("/api/memories")
def list_memories(
    user: CurrentUser, app: Services, source_id: int | None = None
) -> list[Memory]:
    """What the assistant remembers about the signed-in user (only they see it)."""
    return app.metastore.memories.list(user.user.id, source_id)


@router.post("/api/memories", status_code=201)
def add_memory(body: MemoryCreate, user: CurrentUser, app: Services) -> Memory:
    if body.source_id is not None:
        user.require_source(body.source_id)
    saved = app.questions.memory.save(
        user.user.id,
        body.text,
        body.source_id,
        MemoryOrigin.USER,
        app.config.current().memory_max_items,
    )
    if saved is None:
        raise ConfigurationError("Memory is full. Remove something to save more.")
    return saved


@router.put("/api/memories/{memory_id}")
def update_memory(
    memory_id: int, body: MemoryUpdate, user: CurrentUser, app: Services
) -> Memory:
    return app.metastore.memories.update(user.user.id, memory_id, body.text)


@router.delete("/api/memories/{memory_id}", status_code=204)
def delete_memory(memory_id: int, user: CurrentUser, app: Services) -> None:
    app.metastore.memories.delete(user.user.id, memory_id)


@router.delete("/api/memories", status_code=204)
def clear_memories(user: CurrentUser, app: Services) -> None:
    app.metastore.memories.clear(user.user.id)


def _own_chat(app, user, conversation_id: int) -> Conversation:
    chat = app.metastore.conversations.get(conversation_id)
    if chat.user_id != user.user.id:
        raise NotFoundError(f"Chat {conversation_id} does not exist.")
    return chat
