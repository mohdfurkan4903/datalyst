from datalyst.connectors.postgres.introspection import build_schema

COLUMNS = [
    ("public", "customers", "id", "integer", False),
    ("public", "customers", "name", "text", False),
    ("public", "orders", "id", "integer", False),
    ("public", "orders", "customer_id", "integer", False),
    ("audit", "events", "id", "integer", False),
]

CONSTRAINTS = [
    ("public", "customers", "PRIMARY KEY", "id", None, None, None),
    ("public", "orders", "PRIMARY KEY", "id", None, None, None),
    ("public", "orders", "FOREIGN KEY", "customer_id", "public", "customers", "id"),
    ("audit", "events", "PRIMARY KEY", "id", None, None, None),
]


def test_builds_tables_with_keys():
    schema = build_schema(COLUMNS, CONSTRAINTS)

    orders = schema.get_table("public.orders")
    assert orders is not None
    assert [c.name for c in orders.columns] == ["id", "customer_id"]
    assert orders.primary_key == ["id"]
    assert orders.foreign_keys[0].references_table == "public.customers"
    assert orders.foreign_keys[0].references_column == "id"


def test_include_schemas_filters_tables_and_their_constraints():
    schema = build_schema(COLUMNS, CONSTRAINTS, include_schemas={"public"})

    assert [t.qualified_name for t in schema.tables] == [
        "public.customers",
        "public.orders",
    ]
    assert schema.get_table("audit.events") is None


def test_empty_database():
    assert build_schema([], []).tables == []
