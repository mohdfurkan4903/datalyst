import pytest

from datalyst.core.exceptions import (
    ConfigurationError,
    DatalystError,
    UnsupportedDataSourceError,
)
from datalyst.core.interfaces import DataSource
from datalyst.core.models import AnalysisResult, DataSchema, QueryResult, Table


def test_qualified_name():
    assert Table(schema_name="public", name="orders").qualified_name == "public.orders"


def test_get_table_is_case_insensitive():
    schema = DataSchema(tables=[Table(schema_name="public", name="orders")])
    assert schema.get_table("Public.Orders") is schema.tables[0]
    assert schema.get_table("public.missing") is None


def test_success_needs_result_and_no_error():
    result = QueryResult(columns=["n"], rows=[[1]], row_count=1)
    assert AnalysisResult(question="q", result=result).success is True
    assert AnalysisResult(question="q").success is False
    assert AnalysisResult(question="q", result=result, error="boom").success is False


def test_lists_are_not_shared_between_instances():
    a, b = Table(schema_name="s", name="a"), Table(schema_name="s", name="b")
    a.columns.append(None)  # type: ignore[arg-type]
    assert b.columns == []


def test_error_hierarchy():
    assert issubclass(UnsupportedDataSourceError, ConfigurationError)
    assert issubclass(ConfigurationError, DatalystError)


def test_datasource_cannot_be_instantiated_without_methods():
    class Incomplete(DataSource):
        dialect = "x"
        query_language = "x"

    with pytest.raises(TypeError):
        Incomplete()  # type: ignore[abstract]
