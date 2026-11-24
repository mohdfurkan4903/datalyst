import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, UploadFile

from datalyst.api.deps import Admin, AppServices, Services
from datalyst.connectors.files import FileStore, ImportedTable
from datalyst.core.exceptions import ConfigurationError
from datalyst.core.models import DataSourceInfo, LLMDataPolicy

router = APIRouter(prefix="/api/sources", tags=["files"])

CHUNK_BYTES = 1024 * 1024


@router.post("/upload", status_code=201)
def upload_source(
    _: Admin,
    app: Services,
    name: Annotated[str, Form()],
    file: Annotated[UploadFile, File()],
    llm_data_policy: Annotated[LLMDataPolicy, Form()] = LLMDataPolicy.ROWS,
) -> DataSourceInfo:
    """Create a data source from an uploaded file."""
    store = FileStore.create(_root(app))
    try:
        _import(app, store, file)
        return app.metastore.sources.create(
            name, "files", store.url, llm_data_policy=llm_data_policy
        )
    except BaseException:
        store.destroy()
        raise


@router.get("/{source_id}/files")
def list_files(source_id: int, _: Admin, app: Services) -> list[dict]:
    return [
        {
            "table": f.table,
            "size_bytes": f.size_bytes,
            "updated_at": f.updated_at.isoformat(),
        }
        for f in _store(app, source_id).files()
    ]


@router.post("/{source_id}/files", status_code=201)
def add_file(
    source_id: int, _: Admin, app: Services, file: Annotated[UploadFile, File()]
) -> list[dict]:
    """Add a file to an uploaded-files source; a file with the same name replaces
    the table it made before."""
    imported = _import(app, _store(app, source_id), file)
    app.metastore.sources.request_check(source_id)
    return [
        {"table": t.name, "rows": t.rows, "columns": t.columns, "replaced": t.replaced}
        for t in imported
    ]


@router.delete("/{source_id}/files/{table}", status_code=204)
def remove_file(source_id: int, table: str, _: Admin, app: Services) -> None:
    _store(app, source_id).remove(table)
    app.metastore.sources.request_check(source_id)


def delete_files(app: AppServices, source_id: int) -> None:
    """Remove the folder of an uploaded-files source that is being deleted."""
    source = app.metastore.sources.get(source_id)
    if source.kind == "files":
        _store(app, source_id).destroy()


def _root(app: AppServices) -> Path:
    return Path(app.config.current().files_dir).expanduser()


def _store(app: AppServices, source_id: int) -> FileStore:
    source = app.metastore.sources.get(source_id)
    if source.kind != "files":
        raise ConfigurationError("Only uploaded-file sources hold files.")
    url = app.metastore.sources.connection_url(source_id)
    return FileStore.from_url(_root(app), url)


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
