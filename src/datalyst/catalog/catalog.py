import threading
from typing import Protocol

from datalyst.core.models import DataSchema, SyncSummary


class CatalogStore(Protocol):
    def version(self, source_id: int) -> int: ...

    def load_schema(self, source_id: int) -> DataSchema: ...


class Synchronizer(Protocol):
    def sync_now(self) -> SyncSummary: ...


class SchemaCatalog:
    def __init__(
        self, store: CatalogStore, source_id: int, synchronizer: Synchronizer
    ) -> None:
        self._store = store
        self._source_id = source_id
        self._synchronizer = synchronizer
        self._snapshot: tuple[int, DataSchema] | None = None
        self._lock = threading.Lock()

    def get(self, refresh: bool = False) -> DataSchema:
        with self._lock:
            if refresh or self._store.version(self._source_id) == 0:
                self._synchronizer.sync_now()
            version = self._store.version(self._source_id)
            if self._snapshot is None or self._snapshot[0] != version:
                self._snapshot = (version, self._store.load_schema(self._source_id))
            return self._snapshot[1]

    def sync(self) -> SyncSummary:
        with self._lock:
            return self._synchronizer.sync_now()

    @property
    def version(self) -> int | None:
        return self._snapshot[0] if self._snapshot else None
