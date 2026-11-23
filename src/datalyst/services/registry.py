import hashlib
import threading
from collections.abc import Callable

from datalyst.core.models import DataSourceInfo
from datalyst.metastore import Metastore
from datalyst.services.analyst import AnalystService

ServiceFactory = Callable[[DataSourceInfo], AnalystService]


class AnalystRegistry:
    """One AnalystService per data source, rebuilt when its connection or the
    settings change."""

    def __init__(
        self,
        metastore: Metastore,
        factory: ServiceFactory,
        settings_version: Callable[[], int] = lambda: 0,
    ) -> None:
        self._metastore = metastore
        self._factory = factory
        self._settings_version = settings_version
        self._services: dict[int, tuple[str, AnalystService]] = {}
        self._lock = threading.Lock()

    def get(self, source: DataSourceInfo) -> AnalystService:
        identity = self._identity(source)
        with self._lock:
            cached = self._services.get(source.id)
            if cached and cached[0] == identity:
                return cached[1]
            if cached:
                cached[1].close()
            service = self._factory(source)
            self._services[source.id] = (identity, service)
            return service

    def forget(self, source_id: int) -> None:
        with self._lock:
            cached = self._services.pop(source_id, None)
        if cached:
            cached[1].close()

    def close(self) -> None:
        with self._lock:
            services = list(self._services.values())
            self._services.clear()
        for _, service in services:
            service.close()

    def _identity(self, source: DataSourceInfo) -> str:
        url = self._metastore.sources.connection_url(source.id)
        text = (
            f"{url}|{','.join(source.include_schemas)}|{source.llm_data_policy}"
            f"|{self._settings_version()}"
        )
        return hashlib.sha256(text.encode()).hexdigest()
