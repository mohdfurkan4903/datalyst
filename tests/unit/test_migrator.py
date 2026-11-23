from datalyst.metastore import discover_migrations


def test_migrations_are_found_in_order():
    versions = [m.version for m in discover_migrations()]

    assert versions[0] == "0001_catalog"
    assert versions == sorted(versions)
    assert all(m.sql.strip() for m in discover_migrations())
