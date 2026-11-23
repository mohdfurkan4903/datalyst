from collections.abc import Callable
from urllib.parse import urlparse

from datalyst.config import Settings
from datalyst.core.exceptions import UnsupportedDataSourceError
from datalyst.core.interfaces import DataSource

ConnectorFactory = Callable[[str, Settings, list[str]], DataSource]

_factories: dict[str, ConnectorFactory] = {}


def register_connector(*schemes: str) -> Callable[[ConnectorFactory], ConnectorFactory]:
    def decorator(factory: ConnectorFactory) -> ConnectorFactory:
        for scheme in schemes:
            _factories[scheme.lower()] = factory
        return factory

    return decorator


def supported_schemes() -> list[str]:
    return sorted(_factories)


def datasource_kind(url: str) -> str:
    scheme = urlparse(url).scheme.lower().split("+")[0]
    if scheme not in _factories:
        raise UnsupportedDataSourceError(
            f"No connector for '{scheme}://'. Supported: {', '.join(supported_schemes())}."
        )
    return scheme


def create_datasource(
    url: str, settings: Settings, include_schemas: list[str] | None = None
) -> DataSource:
    return _factories[datasource_kind(url)](url, settings, include_schemas or [])


@register_connector("postgresql", "postgres")
def _create_postgres(
    url: str, settings: Settings, include_schemas: list[str]
) -> DataSource:
    from datalyst.connectors.postgres import PostgresDataSource

    return PostgresDataSource(
        url,
        timeout_seconds=settings.query_timeout_seconds,
        max_rows=settings.max_rows,
        include_schemas=include_schemas,
        sample_values=settings.schema_sample_values,
        pool_min_size=settings.pool_min_size,
        pool_max_size=settings.pool_max_size,
    )
