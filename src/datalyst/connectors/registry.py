from collections.abc import Callable
from urllib.parse import urlparse

from datalyst.config import Settings
from datalyst.core.exceptions import UnsupportedDataSourceError
from datalyst.core.interfaces import DataSource

ConnectorFactory = Callable[[str, Settings], DataSource]

_factories: dict[str, ConnectorFactory] = {}


def register_connector(*schemes: str) -> Callable[[ConnectorFactory], ConnectorFactory]:
    def decorator(factory: ConnectorFactory) -> ConnectorFactory:
        for scheme in schemes:
            _factories[scheme.lower()] = factory
        return factory

    return decorator


def supported_schemes() -> list[str]:
    return sorted(_factories)


def create_datasource(settings: Settings) -> DataSource:
    scheme = urlparse(settings.database_url).scheme.lower().split("+")[0]
    factory = _factories.get(scheme)
    if factory is None:
        raise UnsupportedDataSourceError(
            f"No connector for '{scheme}://'. Supported: {', '.join(supported_schemes())}."
        )
    return factory(settings.database_url, settings)


@register_connector("postgresql", "postgres")
def _create_postgres(url: str, settings: Settings) -> DataSource:
    from datalyst.connectors.postgres import PostgresDataSource

    return PostgresDataSource(
        url,
        timeout_seconds=settings.query_timeout_seconds,
        max_rows=settings.max_rows,
        include_schemas=settings.include_schemas,
        pool_min_size=settings.pool_min_size,
        pool_max_size=settings.pool_max_size,
    )
