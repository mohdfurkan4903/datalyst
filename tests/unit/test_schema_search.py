import pytest

from datalyst.catalog import BM25TableSearch
from datalyst.catalog.search import tokenize
from datalyst.core.models import DataSchema
from tests.unit.fakes import large_schema, shop_tables


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("order_items", ["order", "item"]),
        ("OrderItems", ["order", "item"]),
        ("categories", ["category"]),
        ("How many customers are there?", ["customer"]),
        ("address", ["address"]),
    ],
)
def test_tokenize(text, expected):
    assert tokenize(text) == expected


def names(hits):
    return [hit.name for hit in hits]


def test_ranks_table_name_matches_first():
    hits = BM25TableSearch(large_schema()).search(["customers"], limit=3)

    assert names(hits)[0] == "public.customers"


def test_matches_sample_values():
    hits = BM25TableSearch(large_schema()).search(["electronics"], limit=3)

    assert names(hits) == ["public.products"]


def test_matches_descriptions():
    hits = BM25TableSearch(large_schema()).search(["account"], limit=3)

    assert names(hits) == ["public.customers"]


def test_no_match_returns_nothing():
    assert BM25TableSearch(large_schema()).search(["weather"], limit=5) == []


def test_respects_limit_and_sorts_by_score():
    hits = BM25TableSearch(DataSchema(tables=shop_tables())).search(
        ["order", "id"], limit=2
    )

    assert len(hits) == 2
    assert hits[0].score >= hits[1].score


def test_empty_schema():
    assert BM25TableSearch(DataSchema()).search(["anything"], limit=5) == []
