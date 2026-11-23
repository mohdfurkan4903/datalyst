from datalyst.catalog import SchemaRetriever
from datalyst.core.models import DataSchema
from tests.unit.fakes import large_schema, shop_tables


def names(schema: DataSchema) -> list[str]:
    return [table.qualified_name for table in schema.tables]


def test_small_schema_is_returned_whole():
    schema = DataSchema(tables=shop_tables())
    retriever = SchemaRetriever(search_above_tables=30)

    assert not retriever.needs_search(schema)
    assert retriever.retrieve(schema, ["anything"]) is schema


def test_force_searches_small_schema():
    schema = DataSchema(tables=shop_tables())

    picked = SchemaRetriever().retrieve(schema, ["country"], force=True)

    assert names(picked) == ["public.customers"]


def test_large_schema_keeps_only_relevant_tables():
    picked = SchemaRetriever().retrieve(large_schema(), ["customers", "country"])

    assert names(picked)[0] == "public.customers"
    assert all(name.startswith("public.") for name in names(picked))


def test_adds_parent_tables_through_foreign_keys():
    picked = SchemaRetriever().retrieve(large_schema(), ["status"])

    assert names(picked) == ["public.orders", "public.customers"]


def test_bridges_tables_through_join_path():
    picked = SchemaRetriever().retrieve(large_schema(), ["customer", "electronics"])

    assert set(names(picked)) == {
        "public.customers",
        "public.products",
        "public.orders",
        "public.order_items",
    }


def test_respects_max_tables():
    picked = SchemaRetriever(max_tables=2).retrieve(
        large_schema(), ["customer", "electronics"]
    )

    assert len(picked.tables) == 2


def test_nothing_relevant_returns_empty_schema():
    assert SchemaRetriever().retrieve(large_schema(), ["weather"]).tables == []


def test_index_is_reused_for_the_same_schema():
    built = []

    def factory(schema):
        built.append(schema)
        from datalyst.catalog import BM25TableSearch

        return BM25TableSearch(schema)

    retriever = SchemaRetriever(index_factory=factory)
    schema = large_schema()
    retriever.retrieve(schema, ["customers"])
    retriever.retrieve(schema, ["products"])
    retriever.retrieve(large_schema(), ["products"])

    assert len(built) == 2
