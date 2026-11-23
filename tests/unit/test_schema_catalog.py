from datalyst.catalog import SchemaCatalog
from tests.unit.fakes import FakeCatalogStore, FakeDataSource, FakeSynchronizer


def make():
    store, datasource = FakeCatalogStore(), FakeDataSource()
    catalog = SchemaCatalog(store, 1, FakeSynchronizer(store, 1, datasource))
    return catalog, store, datasource


def test_first_use_syncs_from_the_database():
    catalog, store, datasource = make()

    schema = catalog.get()

    assert [t.qualified_name for t in schema.tables] == ["public.orders"]
    assert datasource.schema_loads == 1
    assert store.version(1) == 1


def test_snapshot_is_reused_while_version_is_unchanged():
    catalog, store, datasource = make()

    first = catalog.get()
    second = catalog.get()

    assert first is second
    assert store.loads == 1
    assert datasource.schema_loads == 1


def test_new_version_from_another_process_reloads_snapshot():
    catalog, store, datasource = make()
    catalog.get()

    store.bump(1)
    catalog.get()

    assert store.loads == 2
    assert datasource.schema_loads == 1
    assert catalog.version == 2


def test_refresh_resyncs_from_the_database():
    catalog, store, datasource = make()
    catalog.get()

    catalog.get(refresh=True)

    assert datasource.schema_loads == 2
    assert store.version(1) == 2


def test_sync_returns_summary():
    catalog, _, _ = make()

    assert catalog.sync().tables == 1
