from fastapi import APIRouter

from datalyst.api.deps import Admin, CurrentUser, Services
from datalyst.api.schemas import SourceAccess, SourceCreate, SourceUpdate
from datalyst.connectors import create_datasource, datasource_kind
from datalyst.core.models import DataSourceInfo
from datalyst.sync import check_source_request, describe_source_request

router = APIRouter(prefix="/api/sources", tags=["sources"])


@router.get("")
def list_sources(user: CurrentUser, app: Services) -> list[DataSourceInfo]:
    return [s for s in app.metastore.sources.all() if user.can_use(s.id)]


@router.post("", status_code=201)
def create_source(body: SourceCreate, _: Admin, app: Services) -> DataSourceInfo:
    kind = datasource_kind(body.url)
    _test_connection(app, body.url, body.include_schemas)
    return app.metastore.sources.create(
        body.name,
        kind,
        body.url,
        include_schemas=body.include_schemas,
        llm_data_policy=body.llm_data_policy,
        sync_interval_seconds=body.sync_interval_seconds,
    )


@router.get("/{source_id}")
def get_source(source_id: int, user: CurrentUser, app: Services) -> DataSourceInfo:
    user.require_source(source_id)
    return app.metastore.sources.get(source_id)


@router.patch("/{source_id}")
def update_source(
    source_id: int, body: SourceUpdate, _: Admin, app: Services
) -> DataSourceInfo:
    current = app.metastore.sources.get(source_id)
    if body.url is not None:
        datasource_kind(body.url)
        _test_connection(app, body.url, body.include_schemas or current.include_schemas)
        app.metastore.sources.set_connection_url(source_id, body.url)
    fields = body.model_dump(exclude_unset=True, exclude={"url"})
    updated = app.metastore.sources.update(source_id, **fields)
    app.registry.forget(source_id)
    app.metastore.sources.request_check(source_id)
    return updated


@router.delete("/{source_id}", status_code=204)
def delete_source(source_id: int, _: Admin, app: Services) -> None:
    app.registry.forget(source_id)
    app.metastore.sources.delete(source_id)


@router.get("/{source_id}/access")
def get_access(source_id: int, _: Admin, app: Services) -> SourceAccess:
    open_to_all, user_ids = app.metastore.sources.access(source_id)
    return SourceAccess(open_to_all=open_to_all, user_ids=user_ids)


@router.put("/{source_id}/access")
def set_access(
    source_id: int, body: SourceAccess, _: Admin, app: Services
) -> SourceAccess:
    """Let every user use the source, or only the users listed."""
    app.metastore.sources.set_access(source_id, body.open_to_all, body.user_ids)
    return get_access(source_id, _, app)


@router.post("/{source_id}/sync", status_code=202)
def sync_source(source_id: int, _: Admin, app: Services) -> dict:
    app.metastore.sources.get(source_id)
    queued = app.metastore.jobs.enqueue(check_source_request(source_id))
    return {"queued": queued is not None}


@router.post("/{source_id}/describe", status_code=202)
def describe_source(
    source_id: int, _: Admin, app: Services, force: bool = False
) -> dict:
    app.metastore.sources.get(source_id)
    if force:
        app.metastore.catalog.clear_description_keys(source_id)
    queued = app.metastore.jobs.enqueue(describe_source_request(source_id))
    return {"queued": queued is not None}


@router.get("/{source_id}/status")
def source_status(source_id: int, _: Admin, app: Services) -> dict:
    return {
        "source": app.metastore.sources.get(source_id),
        "jobs": app.metastore.jobs.counts(source_id),
        "recent_jobs": app.metastore.jobs.recent(source_id, 20),
        "recent_changes": app.metastore.catalog.recent_changes(source_id, 20),
        "workers": app.metastore.jobs.workers(),
    }


def _test_connection(app, url: str, include_schemas: list[str]) -> None:
    with create_datasource(url, app.config.current(), include_schemas) as datasource:
        datasource.execute("SELECT 1")
