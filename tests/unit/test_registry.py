import pytest

from datalyst.config import Settings
from datalyst.connectors import create_datasource, supported_schemes
from datalyst.core.exceptions import UnsupportedDataSourceError


def test_postgres_is_registered():
    assert {"postgres", "postgresql"} <= set(supported_schemes())


def test_unknown_scheme_is_rejected():
    with pytest.raises(UnsupportedDataSourceError, match="mongodb"):
        create_datasource(
            Settings(_env_file=None, database_url="mongodb://localhost/db")
        )
