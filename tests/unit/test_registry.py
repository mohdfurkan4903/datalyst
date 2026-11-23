import pytest

from datalyst.config import Settings
from datalyst.connectors import create_datasource, datasource_kind, supported_schemes
from datalyst.core.exceptions import UnsupportedDataSourceError


def test_postgres_is_registered():
    assert {"postgres", "postgresql"} <= set(supported_schemes())


@pytest.mark.parametrize(
    ("url", "kind"),
    [
        ("postgresql://u@h/db", "postgresql"),
        ("postgres://u@h/db", "postgres"),
        ("postgresql+psycopg://u@h/db", "postgresql"),
    ],
)
def test_datasource_kind(url, kind):
    assert datasource_kind(url) == kind


def test_unknown_scheme_is_rejected():
    settings = Settings(_env_file=None, metastore_url="postgresql://localhost/meta")
    with pytest.raises(UnsupportedDataSourceError, match="mongodb"):
        create_datasource("mongodb://localhost/db", settings)
