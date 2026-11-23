from datalyst.connectors.postgres.introspection import (
    build_fingerprints,
    build_schema,
    relation_filter,
)
from datalyst.connectors.postgres.profiling import build_column_stats
from datalyst.core.models import TableKind, TableRef


def column(table, name, data_type="integer", category="N", **extra):
    return {
        "schema_name": extra.get("schema", "public"),
        "table_name": table,
        "column_name": name,
        "data_type": data_type,
        "nullable": extra.get("nullable", False),
        "table_comment": extra.get("table_comment"),
        "column_comment": extra.get("column_comment"),
        "relkind": extra.get("relkind", "r"),
        "type_category": category,
        "row_estimate": extra.get("rows", 10),
    }


def constraint(table, kind, name, ref_table=None, ref_column=None):
    return {
        "schema_name": "public",
        "table_name": table,
        "kind": kind,
        "column_name": name,
        "ref_schema": "public" if ref_table else None,
        "ref_table": ref_table,
        "ref_column": ref_column,
    }


COLUMNS = [
    column("customers", "id", table_comment="Shop accounts"),
    column(
        "customers",
        "name",
        "text",
        "S",
        table_comment="Shop accounts",
        column_comment="Full name",
    ),
    column("orders", "id"),
    column("orders", "customer_id"),
    column("orders", "status", "text", "S"),
    column("orders", "priority"),
    column("order_summary", "total", "numeric", relkind="m", rows=None),
]

CONSTRAINTS = [
    constraint("customers", "PRIMARY KEY", "id"),
    constraint("orders", "PRIMARY KEY", "id"),
    constraint("orders", "FOREIGN KEY", "customer_id", "customers", "id"),
]


def samples(table, name, values):
    return {
        "schema_name": "public",
        "table_name": table,
        "column_name": name,
        "common_values": values,
    }


def test_builds_tables_with_keys_kinds_and_row_estimates():
    schema = build_schema(COLUMNS, CONSTRAINTS)

    orders = schema.get_table("public.orders")
    assert [c.name for c in orders.columns] == [
        "id",
        "customer_id",
        "status",
        "priority",
    ]
    assert orders.primary_key == ["id"]
    assert orders.foreign_keys[0].references_table == "public.customers"
    assert orders.kind == TableKind.TABLE
    assert orders.row_estimate == 10
    summary = schema.get_table("public.order_summary")
    assert summary.kind == TableKind.MATERIALIZED_VIEW
    assert summary.row_estimate is None


def test_reads_table_and_column_descriptions():
    customers = build_schema(COLUMNS, CONSTRAINTS).get_table("public.customers")

    assert customers.description == "Shop accounts"
    assert customers.columns[1].description == "Full name"
    assert customers.columns[0].description is None


def test_sample_values_only_for_text_like_columns_and_short_values():
    rows = [
        samples("orders", "status", ["paid", "shipped", "x" * 100]),
        samples("orders", "priority", ["1", "2"]),
        samples("ghost", "status", ["a"]),
    ]
    orders = build_schema(COLUMNS, CONSTRAINTS, rows).get_table("public.orders")
    by_name = {c.name: c for c in orders.columns}

    assert by_name["status"].sample_values == ["paid", "shipped"]
    assert by_name["priority"].sample_values == []


def test_relation_filter():
    assert relation_filter({"b", "a"}, None) == {
        "schemas": ["a", "b"],
        "ref_schemas": None,
        "ref_names": None,
    }
    refs = [TableRef(schema_name="s", name="t"), TableRef(schema_name="s", name="u")]
    assert relation_filter(set(), refs) == {
        "schemas": [],
        "ref_schemas": ["s", "s"],
        "ref_names": ["t", "u"],
    }


def test_build_fingerprints():
    [fingerprint] = build_fingerprints(
        [
            {
                "schema_name": "public",
                "name": "v",
                "relkind": "v",
                "row_estimate": None,
                "fingerprint": "abc",
            }
        ]
    )
    assert fingerprint.table == TableRef(schema_name="public", name="v")
    assert fingerprint.kind == TableKind.VIEW


def stats_row(name, distinct, inherited=False):
    return {
        "column_name": name,
        "null_fraction": 0.1,
        "distinct_estimate": distinct,
        "common_values": None,
        "common_frequencies": None,
        "histogram_bounds": ["1", "5"],
        "average_width": 4,
        "correlation": 0.9,
        "inherited": inherited,
    }


def test_column_stats_convert_relative_distinct_counts():
    stats = build_column_stats(
        [stats_row("id", -1.0), stats_row("status", 4.0), stats_row("id", 7.0)], 2000
    )

    assert [s.column for s in stats] == ["id", "status"]
    assert stats[0].distinct == 2000
    assert stats[1].distinct == 4
    assert stats[1].common_values == []
    assert build_column_stats([stats_row("x", -0.5)], None)[0].distinct is None


def test_empty_database():
    assert build_schema([], []).tables == []
