import json
from datetime import timedelta

import pytest
from langchain_core.embeddings import DeterministicFakeEmbedding

from datalyst.config import Settings
from datalyst.core.models import (
    ColumnDescription,
    LLMDataPolicy,
    TableDescription,
    TableRef,
)
from datalyst.enrichment.embeddings import vector_literal
from datalyst.sync import (
    DESCRIBE_TABLE,
    EMBED_SOURCE,
    DataSourceProvider,
    JobContext,
    SyncWorker,
    describe_source_request,
)
from tests.unit.fakes import RecordingLLM

pytestmark = pytest.mark.integration

ORDERS = TableRef(schema_name="public", name="orders")


def description(text="AI text", column="AI column"):
    return TableDescription(
        description=text,
        synonyms=["sales"],
        example_questions=["Orders per month?"],
        columns={"status": ColumnDescription(description=column, synonyms=["state"])},
    )


@pytest.fixture
def shop(metastore, database_url, datasource):
    from datalyst.sync import SourceSynchronizer

    source = metastore.sources.create("shop", "postgresql", database_url)
    SourceSynchronizer(
        metastore.catalog, metastore.sources, source.id, datasource
    ).sync_now()
    return source


def table(metastore, source_id, ai=True):
    return metastore.catalog.load_schema(
        source_id, tables=[ORDERS], ai_descriptions=ai
    ).tables[0]


def column(t, name):
    return next(c for c in t.columns if c.name == name)


def test_comments_beat_ai_text_and_human_text_beats_both(metastore, shop):
    metastore.catalog.save_description(shop.id, ORDERS, description(), "k1")

    loaded = table(metastore, shop.id)
    assert loaded.description == "One row per checkout by a customer"
    assert column(loaded, "status").description.startswith("Lifecycle state")
    assert column(loaded, "customer_id").description is None
    assert loaded.synonyms == ["sales"]
    assert column(loaded, "status").synonyms == ["state"]

    with metastore.pool.connection() as conn:
        conn.execute(
            "UPDATE catalog_tables SET description = 'Written by a person',"
            " description_origin = 'human', description_status = 'approved'"
            " WHERE name = 'orders'"
        )
    metastore.catalog.save_description(
        shop.id, ORDERS, description("New AI text"), "k2"
    )
    assert table(metastore, shop.id).description == "Written by a person"


def test_ai_text_is_used_when_there_is_no_comment(metastore, shop):
    customers = TableRef(schema_name="public", name="order_items")
    text = TableDescription(
        description="Lines of an order",
        columns={"quantity": ColumnDescription(description="Units bought")},
    )
    metastore.catalog.save_description(shop.id, customers, text, "k")

    loaded = metastore.catalog.load_schema(shop.id, tables=[customers]).tables[0]
    assert loaded.description == "Products and quantities within an order"
    assert column(loaded, "quantity").description == "Units bought"
    raw = metastore.catalog.load_schema(
        shop.id, tables=[customers], ai_descriptions=False
    )
    assert column(raw.tables[0], "quantity").description is None
    assert raw.tables[0].synonyms == []


def test_description_state(metastore, shop):
    state = metastore.catalog.description_state(shop.id)
    assert state[ORDERS]["description_hash"] is None

    metastore.catalog.save_description(shop.id, ORDERS, description(), "k1")
    state = metastore.catalog.description_state(shop.id)
    assert state[ORDERS]["description_hash"] == "k1"
    assert not state[ORDERS]["incomplete"]

    metastore.catalog.clear_description_keys(shop.id)
    assert (
        metastore.catalog.description_state(shop.id)[ORDERS]["description_hash"] is None
    )


def test_embedding_search_orders_by_similarity(metastore, shop):
    rows = [
        ("table", 1, "h1", vector_literal([1.0, 0.0, 0.0])),
        ("table", 2, "h2", vector_literal([0.0, 1.0, 0.0])),
        ("table", 3, "h3", vector_literal([0.9, 0.1, 0.0])),
        ("column", 4, "h4", vector_literal([1.0, 0.0, 0.0])),
    ]
    metastore.embeddings.save(shop.id, "fake:3", rows)

    hits = metastore.embeddings.search(
        shop.id, "fake:3", vector_literal([1, 0, 0]), "table", 2
    )
    assert [object_id for object_id, _ in hits] == [1, 3]
    assert hits[0][1] == pytest.approx(1.0)
    assert metastore.embeddings.hashes(shop.id, "fake:3")[("table", 2)] == "h2"

    metastore.embeddings.save(
        shop.id, "other:3", [("table", 1, "x", vector_literal([1, 0, 0]))]
    )
    removed = metastore.embeddings.keep_only(
        shop.id, "fake:3", [("table", 1), ("column", 4)]
    )
    assert removed == 3
    assert set(metastore.embeddings.hashes(shop.id, "fake:3")) == {
        ("table", 1),
        ("column", 4),
    }


def describe_reply(columns):
    return json.dumps(
        {
            "description": "AI table",
            "synonyms": ["thing"],
            "columns": {c: {"description": f"AI {c}"} for c in columns},
        }
    )


@pytest.fixture
def worker_for(metastore, metastore_url):
    workers = []

    def make(llm):
        settings = Settings(_env_file=None, metastore_url=metastore_url)
        context = JobContext(
            metastore,
            DataSourceProvider(settings, metastore),
            llm=llm,
            llm_model="fake:llm",
            embeddings=DeterministicFakeEmbedding(size=8),
            embedding_model="fake:8",
            embed_delay=timedelta(0),
        )
        worker = SyncWorker(context, worker_id=f"describe-{len(workers)}")
        workers.append(worker)
        return worker

    yield make
    for worker in workers:
        worker.close()


def test_describe_and_embed_chain_runs_once_per_change(
    metastore, shop, worker_for, datasource
):
    tables = datasource.get_schema().tables
    replies = [describe_reply([c.name for c in t.columns]) for t in tables]
    llm = RecordingLLM(*replies)
    worker = worker_for(llm)
    with metastore.pool.connection() as conn:
        conn.execute("UPDATE data_sources SET next_check_at = now() + interval '1 day'")

    metastore.jobs.enqueue(describe_source_request(shop.id))
    worker.drain()

    jobs = metastore.jobs.recent(shop.id, 100)
    assert sum(j.kind == DESCRIBE_TABLE for j in jobs) == len(tables)
    assert any(j.kind == EMBED_SOURCE and j.status == "done" for j in jobs)
    assert len(llm.prompts) == len(tables)
    assert all("Sample rows (5)" in prompt for prompt in llm.prompts)
    columns = sum(len(t.columns) for t in tables)
    assert len(metastore.embeddings.hashes(shop.id, "fake:8")) == len(tables) + columns
    [usage] = metastore.usage.summary(shop.id)
    assert usage["calls"] == len(tables)

    metastore.jobs.enqueue(describe_source_request(shop.id))
    worker.drain()
    assert len(llm.prompts) == len(tables)


def test_names_policy_sends_no_rows(metastore, shop, worker_for, datasource):
    metastore.sources.update(shop.id, llm_data_policy=LLMDataPolicy.NAMES)
    orders = next(t for t in datasource.get_schema().tables if t.name == "orders")
    llm = RecordingLLM(describe_reply([c.name for c in orders.columns]))
    worker = worker_for(llm)

    from datalyst.sync import describe_table_request

    metastore.jobs.enqueue(describe_table_request(shop.id, ORDERS))
    job = metastore.jobs.claim(worker.worker_id)
    worker.process(job)

    assert "Sample rows" not in llm.prompts[0]
    assert "pending" not in llm.prompts[0]


class RateLimitedEmbeddings(DeterministicFakeEmbedding):
    calls: int = 0
    allowed: int = 1

    def embed_documents(self, texts):
        self.calls += 1
        if self.calls > self.allowed:
            raise RuntimeError("429 rate limited")
        return super().embed_documents(texts)


def test_embedding_continues_later_after_a_rate_limit(
    metastore, shop, worker_for, monkeypatch
):
    from datalyst.sync import embed_source_request, handlers

    monkeypatch.setattr(handlers, "EMBED_BATCH_SIZE", 5)

    worker = worker_for(None)
    limited = RateLimitedEmbeddings(size=8)
    worker._context.embeddings = limited
    metastore.jobs.enqueue(embed_source_request(shop.id, timedelta(0)))

    worker.process(metastore.jobs.claim(worker.worker_id))

    jobs = [j for j in metastore.jobs.recent(shop.id, 10) if j.kind == EMBED_SOURCE]
    assert [j.status for j in jobs] == ["queued", "done"]
    first_pass = len(metastore.embeddings.hashes(shop.id, "fake:8"))
    assert first_pass == 5

    limited.allowed = 100
    with metastore.pool.connection() as conn:
        conn.execute("UPDATE sync_jobs SET run_after = now()")
    worker.drain()
    assert len(metastore.embeddings.hashes(shop.id, "fake:8")) > first_pass
