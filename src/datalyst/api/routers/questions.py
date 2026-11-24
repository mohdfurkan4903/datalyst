from fastapi import APIRouter

from datalyst.api.deps import Admin, CurrentUser, Services
from datalyst.api.routers.knowledge import save_example
from datalyst.api.schemas import AskRequest, Feedback, QueryRequest
from datalyst.core.exceptions import ConfigurationError, NotFoundError
from datalyst.core.models import (
    AnalysisResult,
    HistoryEntry,
    QueryResult,
    VerifiedQuery,
)

router = APIRouter(tags=["questions"])


@router.post("/api/sources/{source_id}/ask")
def ask(
    source_id: int, body: AskRequest, user: CurrentUser, app: Services
) -> AnalysisResult:
    return app.questions.ask(
        user, source_id, body.question.strip(), body.conversation_id
    )


@router.post("/api/sources/{source_id}/query")
def query(
    source_id: int, body: QueryRequest, user: CurrentUser, app: Services
) -> QueryResult:
    return app.questions.run_sql(user, source_id, body.sql)


@router.get("/api/history")
def history(
    user: CurrentUser,
    app: Services,
    source_id: int | None = None,
    user_id: int | None = None,
    everyone: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> list[HistoryEntry]:
    if (everyone or user_id is not None) and not user.is_admin:
        user.require_admin()
    owner = (
        None if everyone else (user_id if user.is_admin and user_id else user.user.id)
    )
    return app.metastore.history.list(
        user_id=owner, source_id=source_id, limit=min(limit, 200), offset=offset
    )


@router.get("/api/history/{entry_id}")
def history_entry(entry_id: int, user: CurrentUser, app: Services) -> HistoryEntry:
    return _own_entry(app, user, entry_id)


@router.post("/api/history/{entry_id}/feedback", status_code=204)
def feedback(entry_id: int, body: Feedback, user: CurrentUser, app: Services) -> None:
    _own_entry(app, user, entry_id)
    app.metastore.history.set_feedback(entry_id, body.value, body.note)


@router.post("/api/history/{entry_id}/promote", status_code=201)
def promote(entry_id: int, admin: Admin, app: Services) -> VerifiedQuery:
    entry = app.metastore.history.get(entry_id)
    if entry.status != "success" or not entry.sql or entry.source_id is None:
        raise ConfigurationError("Only successful answers can become examples.")
    return save_example(
        app, admin.user.email, entry.source_id, entry.question, entry.sql
    )


def _own_entry(app, user, entry_id: int) -> HistoryEntry:
    entry = app.metastore.history.get(entry_id)
    if entry.user_id != user.user.id and not user.is_admin:
        raise NotFoundError(f"History entry {entry_id} does not exist.")
    return entry
