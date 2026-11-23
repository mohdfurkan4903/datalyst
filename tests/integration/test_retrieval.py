import uuid
from datetime import timedelta

import pytest
from langchain_core.embeddings import DeterministicFakeEmbedding

from datalyst.agent import AnalystAgent
from datalyst.catalog import MetastoreRetriever, SchemaCatalog
from datalyst.config import Settings
from datalyst.core.exceptions import NotFoundError
from datalyst.core.models import GlossaryTerm, VerifiedQuery
from datalyst.sync import DataSourceProvider, JobContext, SourceSynchronizer, SyncWorker
from tests.unit.fakes import fake_llm, sql_reply

pytestmark = pytest.mark.integration


@pytest.fixture
def shop(metastore, database_url, metastore_url):
    source = metastore.sources.create("shop", "postgresql", database_url)
    settings = Settings()
    context = JobContext(
        metastore, DataSourceProvider(settings, metastore), infer_delay=timedelta(0)
    )
    worker = SyncWorker(context, worker_id="retrieval-test")
    worker.drain()
    worker.close()
    return source


def test_glossary_crud(metastore, shop):
    knowledge = metastore.knowledge
    knowledge.save_term(shop.id, GlossaryTerm(term="Revenue", definition="paid only"))
    knowledge.save_term(
        shop.id,
        GlossaryTerm(
            term="Revenue", definition="paid and shipped", sql_hint="status IN ('paid')"
        ),
    )

    [term] = knowledge.glossary(shop.id)
    assert (term.term, term.definition) == ("Revenue", "paid and shipped")
    knowledge.delete_term(shop.id, "revenue")
    assert knowledge.glossary(shop.id) == []
    with pytest.raises(NotFoundError):
        knowledge.delete_term(shop.id, "revenue")


def test_examples_crud(metastore, shop):
    knowledge = metastore.knowledge
    first = knowledge.save_example(
        shop.id,
        VerifiedQuery(question="Orders?", sql="SELECT 1", tables=["public.orders"]),
    )
    second = knowledge.save_example(
        shop.id, VerifiedQuery(question="Customers?", sql="SELECT 2")
    )

    assert [e.id for e in knowledge.examples(shop.id, [second.id, first.id])] == [
        second.id,
        first.id,
    ]
    knowledge.delete_example(shop.id, first.id)
    assert [e.question for e in knowledge.examples(shop.id)] == ["Customers?"]


def test_value_matches_find_real_values(metastore, shop):
    hints = metastore.knowledge.value_matches(
        shop.id, ["electronic", "cancelled", "zzz"]
    )

    found = {(h.term, h.table, h.column, h.value) for h in hints}
    assert ("electronic", "public.products", "category", "Electronics") in found
    assert ("cancelled", "public.orders", "status", "cancelled") in found
    assert not any(h.term == "zzz" for h in hints)


def retriever(metastore, source, **options):
    return MetastoreRetriever(
        source.id,
        metastore.knowledge,
        metastore.embeddings,
        DeterministicFakeEmbedding(size=8),
        "fake:8",
        **options,
    )


def test_candidates_carry_hints_terms_and_examples(metastore, shop):
    metastore.knowledge.save_term(
        shop.id, GlossaryTerm(term="revenue", definition="paid and shipped orders")
    )
    metastore.knowledge.save_example(
        shop.id,
        VerifiedQuery(
            question="Revenue from electronics by month",
            sql="SELECT 1",
            tables=["public.products"],
        ),
    )
    schema = metastore.catalog.load_schema(shop.id)

    candidates = retriever(metastore, shop).candidates(
        schema, "What is our revenue from electronics?"
    )

    assert not candidates.searched
    assert [t.term for t in candidates.glossary] == ["revenue"]
    assert candidates.value_hints[0].value == "Electronics"
    assert [e.question for e in candidates.examples] == [
        "Revenue from electronics by month"
    ]


def test_search_ranks_value_and_keyword_matches(metastore, shop):
    schema = metastore.catalog.load_schema(shop.id)

    candidates = retriever(metastore, shop, search_above_tables=2).candidates(
        schema, "electronics sold per customer country"
    )

    assert candidates.searched
    assert {"public.products", "public.customers"} <= set(candidates.tables[:3])
    assembled = retriever(metastore, shop, search_above_tables=2).assemble(
        schema, ["public.products", "public.customers"]
    )
    assert {t.name for t in assembled.tables} >= {
        "products",
        "customers",
        "orders",
        "order_items",
    }


def test_dropped_column_heals_and_retries(
    metastore, database_url, datasource, shop_admin
):
    name = f"probe_{uuid.uuid4().hex[:8]}"
    shop_admin.execute(f"CREATE TABLE {name} (id int PRIMARY KEY, note text)")
    try:
        source = metastore.sources.create("heal", "postgresql", database_url)
        synchronizer = SourceSynchronizer(
            metastore.catalog, metastore.sources, source.id, datasource
        )
        catalog = SchemaCatalog(metastore.catalog, source.id, synchronizer)
        catalog.get()
        shop_admin.execute(f"ALTER TABLE {name} DROP COLUMN note")

        def heal(_tables):
            synchronizer.sync_now()
            return catalog.get()

        llm = fake_llm(
            sql_reply(f"SELECT note FROM public.{name}"),
            sql_reply(f"SELECT id FROM public.{name}"),
        )
        agent = AnalystAgent(llm, datasource, healer=heal)

        state = agent.run("notes?", catalog.get())

        assert state["healed"]
        assert state["result"] is not None
        heal_prompt = state["messages"][-2].content
        assert "schema changed" in heal_prompt
        assert f"public.{name}\n  id integer PK" in heal_prompt
        assert "note text" not in heal_prompt
        table = catalog.get().get_table(f"public.{name}")
        assert [c.name for c in table.columns] == ["id"]
    finally:
        shop_admin.execute(f"DROP TABLE IF EXISTS {name}")
