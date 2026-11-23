import pytest

from datalyst.catalog import SchemaCatalog
from datalyst.core.exceptions import ConfigurationError, NotFoundError
from datalyst.core.models import (
    Column,
    DataSchema,
    ForeignKey,
    LLMDataPolicy,
    SourceStatus,
    Table,
    TableRef,
)
from datalyst.metastore import Metastore
from datalyst.sync import SourceSynchronizer

pytestmark = pytest.mark.integration

URL = "postgresql://ro:topsecret@db.internal:5432/sales"


def add(metastore, name="sales", **kwargs):
    return metastore.sources.create(name, "postgresql", URL, **kwargs)


def test_migrations_are_idempotent(metastore):
    assert metastore.migrate() == []
    assert metastore.pending_migrations() == []


def test_create_and_read_source(metastore):
    created = add(
        metastore, include_schemas=["public"], llm_data_policy=LLMDataPolicy.STATS
    )

    fetched = metastore.sources.get_by_name("sales")
    assert fetched == created
    assert fetched.include_schemas == ["public"]
    assert fetched.llm_data_policy == LLMDataPolicy.STATS
    assert fetched.status == SourceStatus.NEW
    assert metastore.sources.connection_url(created.id) == URL


def test_connection_url_is_encrypted_at_rest(metastore):
    add(metastore)

    with metastore.pool.connection() as conn:
        stored = conn.execute(
            "SELECT connection_encrypted FROM data_sources"
        ).fetchone()
    assert "topsecret" not in stored["connection_encrypted"]


def test_duplicate_and_invalid_names_are_rejected(metastore):
    add(metastore)

    with pytest.raises(ConfigurationError, match="exists"):
        add(metastore)
    with pytest.raises(ConfigurationError):
        add(metastore, name="Bad Name!")


def test_resolve_by_name_or_single_source(metastore):
    with pytest.raises(NotFoundError):
        metastore.sources.resolve(None)

    first = add(metastore)
    assert metastore.sources.resolve(None) == first

    add(metastore, name="crm")
    with pytest.raises(ConfigurationError, match="crm, sales"):
        metastore.sources.resolve(None)
    assert metastore.sources.resolve("crm").name == "crm"


def test_update_and_delete_source(metastore):
    source = add(metastore)

    updated = metastore.sources.update(source.id, sync_interval_seconds=600)
    assert updated.sync_interval_seconds == 600
    with pytest.raises(ConfigurationError):
        metastore.sources.update(source.id, name="other")

    metastore.sources.delete(source.id)
    with pytest.raises(NotFoundError):
        metastore.sources.get(source.id)


def test_reading_url_without_secret_key_is_a_configuration_error(
    metastore_url, metastore
):
    source = add(metastore)

    with (
        Metastore.connect(metastore_url) as keyless,
        pytest.raises(ConfigurationError, match="SECRET_KEY"),
    ):
        keyless.sources.connection_url(source.id)


def shop(*, with_orders=True, extra_column=False):
    customers = Table(
        schema_name="public",
        name="customers",
        description="People",
        columns=[
            Column(name="id", data_type="integer"),
            Column(name="country", data_type="text", sample_values=["UK", "India"]),
            *([Column(name="tier", data_type="text")] if extra_column else []),
        ],
        primary_key=["id"],
    )
    orders = Table(
        schema_name="public",
        name="orders",
        columns=[
            Column(name="id", data_type="integer"),
            Column(name="customer_id", data_type="integer", nullable=False),
        ],
        primary_key=["id"],
        foreign_keys=[
            ForeignKey(
                column="customer_id",
                references_table="public.customers",
                references_column="id",
            )
        ],
    )
    return DataSchema(tables=[customers, *([orders] if with_orders else [])])


def apply(metastore, source_id, schema, fingerprint="v1"):
    fingerprints = {table.ref: fingerprint for table in schema.tables}
    return metastore.catalog.apply_tables(source_id, schema.tables, fingerprints)


def test_catalog_round_trip(metastore):
    source = add(metastore)

    summary = apply(metastore, source.id, shop())

    assert summary.added == ["public.customers", "public.orders"]
    assert metastore.catalog.load_schema(source.id) == shop()
    assert metastore.catalog.version(source.id) == 1
    assert set(metastore.catalog.fingerprints(source.id).values()) == {"v1"}


def test_unchanged_tables_do_not_bump_the_version(metastore):
    source = add(metastore)
    apply(metastore, source.id, shop())

    summary = apply(metastore, source.id, shop())

    assert not summary.changed
    assert metastore.catalog.version(source.id) == 1


def test_column_changes_are_recorded_and_descriptions_go_stale(metastore):
    source = add(metastore)
    apply(metastore, source.id, shop())
    with metastore.pool.connection() as conn:
        conn.execute(
            "UPDATE catalog_tables SET description = 'x', description_status = 'approved',"
            " description_origin = 'human' WHERE name = 'customers'"
        )

    changed = shop(extra_column=True)
    fingerprints = {
        t.ref: "v2" if t.name == "customers" else "v1" for t in changed.tables
    }
    summary = metastore.catalog.apply_tables(source.id, changed.tables, fingerprints)

    assert summary.altered == ["public.customers"]
    assert summary.added == []
    with metastore.pool.connection() as conn:
        change = conn.execute(
            "SELECT table_name, change, details FROM schema_changes ORDER BY id DESC"
        ).fetchone()
        status = conn.execute(
            "SELECT description_status FROM catalog_tables WHERE name = 'customers'"
        ).fetchone()
    assert change == {
        "table_name": "customers",
        "change": "altered",
        "details": {"added": ["tier"]},
    }
    assert status["description_status"] == "stale"
    assert metastore.catalog.version(source.id) == 2


def test_remove_and_restore_tables(metastore):
    source = add(metastore)
    apply(metastore, source.id, shop())

    removed = metastore.catalog.remove_tables(
        source.id, [TableRef(schema_name="public", name="orders")]
    )
    assert removed == ["public.orders"]
    assert [t.name for t in metastore.catalog.load_schema(source.id).tables] == [
        "customers"
    ]
    assert (
        metastore.catalog.remove_tables(
            source.id, [TableRef(schema_name="public", name="orders")]
        )
        == []
    )

    summary = apply(metastore, source.id, shop())
    assert summary.added == ["public.orders"]
    assert metastore.catalog.load_schema(source.id) == shop()


def test_catalogs_are_isolated_per_source(metastore):
    sales, crm = add(metastore), add(metastore, name="crm")
    apply(metastore, sales.id, shop())

    assert metastore.catalog.load_schema(crm.id).tables == []


def test_schema_catalog_syncs_real_database_into_metastore(
    metastore, datasource, database_url
):
    source = metastore.sources.create("shop", "postgresql", database_url)
    synchronizer = SourceSynchronizer(
        metastore.catalog, metastore.sources, source.id, datasource
    )
    catalog = SchemaCatalog(metastore.catalog, source.id, synchronizer)

    schema = catalog.get()

    orders = schema.get_table("public.orders")
    assert {t.qualified_name for t in schema.tables} >= {
        "public.customers",
        "public.orders",
        "public.order_items",
        "public.products",
    }
    assert orders.description == "One row per checkout by a customer"
    assert any(fk.references_table == "public.customers" for fk in orders.foreign_keys)
    assert schema == datasource.get_schema()
    assert not catalog.sync().changed
