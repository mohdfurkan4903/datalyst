import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, UploadFile

from datalyst.api.deps import AppServices, CurrentUser, Services
from datalyst.connectors.files import FileStore, ImportedTable
from datalyst.core.exceptions import (
    ConfigurationError,
    NotFoundError,
    PermissionDeniedError,
)
from datalyst.core.models import DataSourceInfo, LLMDataPolicy
from datalyst.security import Principal

router = APIRouter(prefix="/api/sources", tags=["files"])

CHUNK_BYTES = 1024 * 1024
PERSONAL_PREFIX = "uploads-"
MAX_FILES_PER_ATTACH = 10


def own_store(source_id: int, user: CurrentUser, app: Services) -> FileStore:
    """The folder of a source the caller may manage. This runs before the request
    body is read, so a stranger is refused without uploading anything."""
    source = app.metastore.sources.get(source_id)
    user.require_owner(source.owner_id)
    if source.kind != "files":
        raise ConfigurationError("Only uploaded-file sources hold files.")
    url = app.metastore.sources.connection_url(source_id)
    return FileStore.from_url(_root(app), url)


OwnStore = Annotated[FileStore, Depends(own_store)]


@router.post("/upload", status_code=201)
def upload_source(
    user: CurrentUser,
    app: Services,
    name: Annotated[str, Form()],
    file: Annotated[UploadFile, File()],
    llm_data_policy: Annotated[LLMDataPolicy, Form()] = LLMDataPolicy.ROWS,
) -> DataSourceInfo:
    """Create a data source from an uploaded file. A user's own upload is private
    to them (and admins); an admin's is open to everyone until they change it."""
    _check_may_upload(app, user)
    store = FileStore.create(_root(app))
    try:
        _import(app, store, file)
        return app.metastore.sources.create(
            name,
            "files",
            store.url,
            llm_data_policy=llm_data_policy,
            owner_id=user.user.id,
            open_to_all=user.is_admin,
        )
    except BaseException:
        store.destroy()
        raise


@router.post("/personal/files", status_code=201)
def attach_files(
    user: CurrentUser,
    app: Services,
    files: Annotated[list[UploadFile], File()],
) -> dict:
    """Files attached in a chat. They go to the user's personal source (made the
    first time), which is private to them, so they can ask about them at once."""
    if len(files) > MAX_FILES_PER_ATTACH:
        raise ConfigurationError(
            f"Attach up to {MAX_FILES_PER_ATTACH} files at a time."
        )
    _check_may_upload(app, user, personal=True)
    source, store, created = _personal_source(app, user)
    tables: list[ImportedTable] = []
    try:
        for upload in files:
            tables += _import(app, store, upload)
    except BaseException:
        if created and not store.files():
            app.metastore.sources.delete(source.id)
            store.destroy()
        raise
    app.metastore.sources.request_check(source.id)
    return {
        "source": source,
        "tables": [
            {
                "table": t.name,
                "rows": t.rows,
                "columns": t.columns,
                "replaced": t.replaced,
            }
            for t in tables
        ],
    }


@router.get("/{source_id}/files")
def list_files(store: OwnStore) -> list[dict]:
    return [
        {
            "table": f.table,
            "size_bytes": f.size_bytes,
            "updated_at": f.updated_at.isoformat(),
        }
        for f in store.files()
    ]


@router.post("/{source_id}/files", status_code=201)
def add_file(
    source_id: int,
    store: OwnStore,
    app: Services,
    file: Annotated[UploadFile, File()],
) -> list[dict]:
    """Add a file to an uploaded-files source; a file with the same name replaces
    the table it made before."""
    imported = _import(app, store, file)
    app.metastore.sources.request_check(source_id)
    return [
        {"table": t.name, "rows": t.rows, "columns": t.columns, "replaced": t.replaced}
        for t in imported
    ]


@router.delete("/{source_id}/files/{table}", status_code=204)
def remove_file(source_id: int, table: str, store: OwnStore, app: Services) -> None:
    store.remove(table)
    app.metastore.sources.request_check(source_id)


def delete_files(app: AppServices, source_id: int) -> None:
    """Remove the folder of an uploaded-files source that is being deleted."""
    source = app.metastore.sources.get(source_id)
    if source.kind == "files":
        url = app.metastore.sources.connection_url(source_id)
        FileStore.from_url(_root(app), url).destroy()


def personal_name(user_id: int) -> str:
    return f"{PERSONAL_PREFIX}{user_id}"


def _personal_source(
    app: AppServices, user: Principal
) -> tuple[DataSourceInfo, FileStore, bool]:
    """The user's own upload source and its folder, creating both on first use."""
    name = personal_name(user.user.id)
    sources = app.metastore.sources
    try:
        source = sources.get_by_name(name)
    except NotFoundError:
        store = FileStore.create(_root(app))
        try:
            source = sources.create(
                name,
                "files",
                store.url,
                owner_id=user.user.id,
                open_to_all=False,
            )
        except BaseException:
            store.destroy()
            raise
        return source, store, True
    if source.owner_id != user.user.id or source.kind != "files":
        raise ConfigurationError("Your uploads could not be set up. Ask an admin.")
    url = sources.connection_url(source.id)
    return source, FileStore.from_url(_root(app), url), False


def _check_may_upload(
    app: AppServices, user: Principal, personal: bool = False
) -> None:
    settings = app.config.current()
    if not user.is_admin and not settings.files_user_uploads:
        raise PermissionDeniedError(
            "Uploading files is turned off. Ask an admin to turn it on."
        )
    if user.is_admin or personal:
        return
    mine = personal_name(user.user.id)
    owned = [
        s
        for s in app.metastore.sources.owned_by(user.user.id, "files")
        if s.name != mine
    ]
    if len(owned) >= settings.files_max_user_sources:
        raise ConfigurationError(
            f"You can have up to {settings.files_max_user_sources} uploaded sources. "
            "Delete one, or add more files to an existing source."
        )


def _root(app: AppServices) -> Path:
    return Path(app.config.current().files_dir).expanduser()


def _import(
    app: AppServices, store: FileStore, upload: UploadFile
) -> list[ImportedTable]:
    limit = app.config.current().files_max_upload_mb * 1024 * 1024
    filename = upload.filename or "upload"
    suffix = Path(filename).suffix.lower()
    with tempfile.TemporaryDirectory() as scratch:
        target = Path(scratch) / f"upload{suffix}"
        size = 0
        with target.open("wb") as out:
            while chunk := upload.file.read(CHUNK_BYTES):
                size += len(chunk)
                if size > limit:
                    raise ConfigurationError(
                        f"'{filename}' is larger than the {limit // 1024 // 1024} MB limit."
                    )
                out.write(chunk)
        return store.add(target, filename)
