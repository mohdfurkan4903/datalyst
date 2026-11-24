import shutil
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from datalyst.connectors.files.connector import FOLDER, TABLE_FILE_SUFFIX
from datalyst.connectors.files.importer import ImportedTable, import_file
from datalyst.core.exceptions import ConfigurationError, NotFoundError

SCHEME = "files"


@dataclass(frozen=True)
class StoredFile:
    table: str
    size_bytes: int
    updated_at: datetime


class FileStore:
    """The folder holding one uploaded-files data source."""

    def __init__(self, root: Path, folder: str) -> None:
        if not FOLDER.match(folder):
            raise ConfigurationError("Not an uploaded-files data source.")
        self.folder = folder
        self.directory = (Path(root) / folder).resolve()

    @classmethod
    def create(cls, root: Path) -> "FileStore":
        store = cls(root, uuid.uuid4().hex)
        store.directory.mkdir(parents=True, exist_ok=False)
        return store

    @classmethod
    def from_url(cls, root: Path, url: str) -> "FileStore":
        prefix = f"{SCHEME}://"
        if not url.startswith(prefix):
            raise ConfigurationError("Not an uploaded-files data source.")
        return cls(root, url.removeprefix(prefix).strip("/"))

    @property
    def url(self) -> str:
        return f"{SCHEME}://{self.folder}"

    def add(self, upload: Path, filename: str) -> list[ImportedTable]:
        return import_file(upload, filename, self.directory)

    def files(self) -> list[StoredFile]:
        return [
            StoredFile(
                table=path.stem,
                size_bytes=path.stat().st_size,
                updated_at=datetime.fromtimestamp(path.stat().st_mtime, UTC),
            )
            for path in sorted(self.directory.glob(f"*{TABLE_FILE_SUFFIX}"))
        ]

    def remove(self, table: str) -> None:
        path = self.directory / f"{table}{TABLE_FILE_SUFFIX}"
        if path.parent != self.directory or not path.is_file():
            raise NotFoundError(f"There is no table '{table}' in this source.")
        path.unlink()

    def destroy(self) -> None:
        shutil.rmtree(self.directory, ignore_errors=True)
