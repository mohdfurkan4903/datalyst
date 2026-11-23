from datetime import timedelta
from pathlib import Path

import pytest
import yaml

from datalyst.config import Settings
from datalyst.core.models import (
    ColumnRef,
    InferredRelationship,
    RelationshipOrigin,
    SemanticType,
    TableRef,
)
from datalyst.enrichment import pipeline
from datalyst.sync import (
    INFER_RELATIONSHIPS,
    PROFILE_TABLE,
    DataSourceProvider,
    JobContext,
    SyncWorker,
)

pytestmark = pytest.mark.integration

GROUND_TRUTH = Path(__file__).parents[1] / "fixtures" / "erp_relationships.yaml"


def edges(kind):
    data = yaml.safe_load(GROUND_TRUTH.read_text())
    return {
        tuple(f"public.{side.strip()}" for side in edge.split("->"))
        for edge in data[kind]
    }


@pytest.fixture(scope="module")
def profiled_warehouse(warehouse_url):
    from datalyst.connectors.postgres import PostgresDataSource

    with PostgresDataSource(warehouse_url, timeout_seconds=10) as ds:
        schema = ds.get_schema()
        for table in schema.tables:
            result = pipeline.profile(ds, table)
            for column in table.columns:
                column.profile = result.profiles[column.name]
        yield ds, schema


def test_profiles_explain_cryptic_columns(profiled_warehouse):
    _, schema = profiled_warehouse

    def semantic(table, column):
        found = next(
            c for c in schema.get_table(f"public.{table}").columns if c.name == column
        )
        return found.profile.semantic_type, found.profile.format

    assert semantic("ord_hdr", "ord_dt") == (SemanticType.DATE_TEXT, "YYYYMMDD")
    assert semantic("ord_hdr", "tot_amt_cents")[0] == SemanticType.MONEY_CENTS
    assert semantic("cust_mstr", "eml_addr")[0] == SemanticType.EMAIL
    assert semantic("cust_mstr", "ctry_cd")[0] == SemanticType.COUNTRY_CODE
    assert semantic("cust_addr", "is_dflt")[0] == SemanticType.BOOLEAN_FLAG
    assert semantic("ord_hdr", "sts_cd")[0] == SemanticType.CATEGORY
    assert semantic("pmt_txn", "gw_resp")[0] == SemanticType.JSON
    assert semantic("stg_imp_2023", "raw_dt") == (SemanticType.DATE_TEXT, "DD/MM/YYYY")
    assert semantic("shp_trk", "trk_url")[0] == SemanticType.URL


def test_inferred_relationships_match_ground_truth(profiled_warehouse):
    ds, schema = profiled_warehouse

    found = {
        (r.child.qualified_name, r.parent.qualified_name)
        for r in pipeline.relationships(ds, schema)
    }

    truth, traps = edges("relationships"), edges("traps")
    precision = len(found & truth) / len(found)
    recall = len(found & truth) / len(truth)
    print(f"precision={precision:.3f} recall={recall:.3f}")
    assert precision >= 0.95
    assert recall >= 0.95
    assert not found & traps


@pytest.fixture
def shop_worker(metastore, metastore_url, database_url):
    source = metastore.sources.create("shop", "postgresql", database_url)
    settings = Settings()
    context = JobContext(
        metastore, DataSourceProvider(settings, metastore), infer_delay=timedelta(0)
    )
    worker = SyncWorker(context, worker_id="enrichment-test")
    yield source, worker
    worker.close()


def test_sync_chains_into_profiling_and_inference(metastore, shop_worker):
    source, worker = shop_worker

    worker.drain()

    kinds = [job.kind for job in metastore.jobs.recent(source.id, 100)]
    assert kinds.count(PROFILE_TABLE) >= 4
    assert kinds.count(INFER_RELATIONSHIPS) >= 1
    products = metastore.catalog.load_schema(
        source.id, tables=[TableRef(schema_name="public", name="products")]
    ).tables[0]
    category = next(c for c in products.columns if c.name == "category")
    assert category.profile.semantic_type == SemanticType.CATEGORY
    assert "Electronics" in category.sample_values
    with metastore.pool.connection() as conn:
        match = conn.execute(
            """
            SELECT v.value FROM column_values v
            JOIN catalog_columns c ON c.id = v.column_id
            WHERE c.name = 'category' AND v.value %% %s
            """,
            ("electronic",),
        ).fetchone()
    assert match["value"] == "Electronics"


def test_rejected_and_declared_relationships_are_kept(metastore, shop_worker):
    source, worker = shop_worker
    worker.drain()
    catalog = metastore.catalog

    def ref(table, column):
        return ColumnRef(
            table=TableRef(schema_name="public", name=table), column=column
        )

    guess = InferredRelationship(
        child=ref("order_items", "quantity"),
        parent=ref("customers", "id"),
        confidence=0.9,
    )
    declared = InferredRelationship(
        child=ref("orders", "customer_id"),
        parent=ref("customers", "id"),
        confidence=0.5,
    )
    catalog.replace_inferred_relationships(source.id, [guess, declared])
    with metastore.pool.connection() as conn:
        conn.execute(
            "UPDATE relationships SET status = 'rejected' WHERE origin = 'inferred'"
        )
    catalog.replace_inferred_relationships(source.id, [guess])

    rows = {(r["child"], r["parent"]): r for r in catalog.relationships(source.id)}
    rejected = rows[("public.order_items.quantity", "public.customers.id")]
    assert rejected["status"] == "rejected"
    kept = rows[("public.orders.customer_id", "public.customers.id")]
    assert (kept["origin"], kept["confidence"]) == ("declared", 1.0)
    joins = catalog.load_schema(
        source.id, tables=[TableRef(schema_name="public", name="order_items")]
    )
    assert {fk.column for fk in joins.tables[0].foreign_keys} == {
        "order_id",
        "product_id",
    }

    catalog.replace_inferred_relationships(source.id, [])
    assert ("public.order_items.quantity", "public.customers.id") in {
        (r["child"], r["parent"]) for r in catalog.relationships(source.id)
    }
    declared_only = catalog.load_schema(
        source.id, min_confidence=0, origins=[RelationshipOrigin.DECLARED]
    )
    assert sum(len(t.foreign_keys) for t in declared_only.tables) == 3
