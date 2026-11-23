import hashlib
import threading

from datalyst.config import Settings
from datalyst.connectors import create_datasource
from datalyst.core.exceptions import ConfigurationError
from datalyst.core.interfaces import CatalogIntrospector, DataSource
from datalyst.core.models import DataSourceInfo
from datalyst.metastore import Metastore


class DataSourceProvider:
    def __init__(self, settings: Settings, metastore: Metastore) -> None:
        self._settings = settings
        self._metastore = metastore
        self._open: dict[int, tuple[str, DataSource]] = {}
        self._lock = threading.Lock()

    def get(self, source_id: int) -> tuple[DataSourceInfo, DataSource]:
        info = self._metastore.sources.get(source_id)
        url = self._metastore.sources.connection_url(source_id)
        identity = hashlib.sha256(
            f"{url}|{','.join(info.include_schemas)}".encode()
        ).hexdigest()
        with self._lock:
            cached = self._open.get(source_id)
            if cached and cached[0] == identity:
                return info, cached[1]
            if cached:
                cached[1].close()
            datasource = create_datasource(url, self._settings, info.include_schemas)
            self._open[source_id] = (identity, datasource)
        return info, datasource

    def introspector(self, source_id: int) -> CatalogIntrospector:
        _, datasource = self.get(source_id)
        if not isinstance(datasource, CatalogIntrospector):
            raise ConfigurationError(
                f"The {type(datasource).__name__} connector cannot be synced."
            )
        return datasource

    def close(self) -> None:
        with self._lock:
            for _, datasource in self._open.values():
                datasource.close()
            self._open.clear()
