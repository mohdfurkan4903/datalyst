import pytest
from langchain_core.messages import HumanMessage

from datalyst.agent import AnalystAgent, ErrorKind
from datalyst.catalog import SchemaRetriever
from datalyst.catalog.retriever import Signals
from datalyst.core.exceptions import LLMError, QueryExecutionError
from datalyst.core.models import (
    Column,
    DataSchema,
    GlossaryTerm,
    QueryResult,
    Table,
    ValueHint,
    VerifiedQuery,
)
from tests.unit.fakes import (
    FakeDataSource,
    failing_on,
    fake_llm,
    large_schema,
    sql_reply,
)

SCHEMA = DataSchema(
    tables=[
        Table(
            schema_name="public",
            name="orders",
            columns=[Column(name="id", data_type="integer")],
            primary_key=["id"],
        )
    ]
)


def run(llm, datasource, max_attempts=3):
    return AnalystAgent(llm, datasource, max_attempts=max_attempts).run(
        "How many orders?", SCHEMA
    )


def test_happy_path_uses_one_attempt():
    datasource = FakeDataSource()
    state = run(fake_llm(sql_reply("SELECT count(*) FROM public.orders")), datasource)

    assert state["attempts"] == 1
    assert state["error"] is None
    assert state["result"].rows == [[1]]
    assert datasource.executed == ["SELECT count(*) FROM public.orders"]


def test_small_schema_is_sent_whole_without_rewrite():
    state = run(fake_llm(sql_reply("SELECT 1")), FakeDataSource())

    assert state["tables"] == ["public.orders"]
    assert "rewritten_question" not in state
    assert "public.orders\n  id integer PK" in state["messages"][1].content


def test_repairs_after_execution_error():
    bad, good = "SELECT x FROM public.orders", "SELECT id FROM public.orders"
    datasource = FakeDataSource(execute=failing_on(bad))
    state = run(fake_llm(sql_reply(bad), sql_reply(good)), datasource)

    assert state["attempts"] == 2
    assert state["query"] == good
    assert state["result"] is not None
    repair = state["messages"][-2]
    assert isinstance(repair, HumanMessage)
    assert "does not exist" in repair.content


def test_repairs_after_unsafe_query_without_executing_it():
    datasource = FakeDataSource()
    state = run(
        fake_llm(sql_reply("DELETE FROM public.orders"), sql_reply("SELECT 1")),
        datasource,
    )

    assert state["attempts"] == 2
    assert state["result"] is not None
    assert datasource.executed == ["SELECT 1"]


def test_gives_up_after_max_attempts():
    bad = "SELECT id FROM public.orders WHERE id > 0"
    datasource = FakeDataSource(execute=failing_on(bad))
    state = run(fake_llm(*[sql_reply(bad)] * 3), datasource, max_attempts=3)

    assert state["attempts"] == 3
    assert state["result"] is None
    assert state["error_kind"] == ErrorKind.EXECUTION
    assert len(datasource.executed) == 3


def test_unsafe_query_is_never_executed_even_when_attempts_run_out():
    datasource = FakeDataSource()
    state = run(
        fake_llm(sql_reply("DROP TABLE public.orders")), datasource, max_attempts=1
    )

    assert state["error_kind"] == ErrorKind.UNSAFE
    assert datasource.executed == []


def test_refusal_stops_without_retry():
    datasource = FakeDataSource()
    state = run(fake_llm("CANNOT_ANSWER: read-only access only"), datasource)

    assert state["attempts"] == 1
    assert state["error_kind"] == ErrorKind.REFUSED
    assert state["error"] == "Declined: read-only access only"
    assert datasource.executed == []


def test_llm_failure_raises_llm_error():
    with pytest.raises(LLMError):
        run(fake_llm(), FakeDataSource())


def test_large_schema_rewrites_and_sends_only_relevant_tables():
    llm = fake_llm(
        "QUESTION: Which clients bought the most electronics?\n"
        "TERMS: customers, orders, order items, products, electronics",
        sql_reply("SELECT 1"),
    )
    agent = AnalystAgent(llm, FakeDataSource(), retriever=SchemaRetriever())

    state = agent.run("who buys gadgets", large_schema())

    assert state["rewritten_question"] == "Which clients bought the most electronics?"
    assert set(state["tables"]) == {
        "public.customers",
        "public.orders",
        "public.order_items",
        "public.products",
    }
    prompt = state["messages"][1].content
    assert "public.order_items" in prompt
    assert "telemetry_batch" not in prompt
    assert "Question: who buys gadgets" in prompt


def test_rewrite_can_be_disabled_for_large_schemas():
    agent = AnalystAgent(
        fake_llm(sql_reply("SELECT 1")),
        FakeDataSource(),
        retriever=SchemaRetriever(),
        rewrite_question=False,
    )

    state = agent.run("customers per country", large_schema())

    assert "rewritten_question" not in state
    assert "public.customers" in state["tables"]
    assert state["result"] is not None


def test_unknown_columns_are_caught_before_execution():
    datasource = FakeDataSource()
    state = run(
        fake_llm(
            sql_reply("SELECT total FROM public.orders"),
            sql_reply("SELECT id FROM public.orders"),
        ),
        datasource,
    )

    assert datasource.executed == ["SELECT id FROM public.orders"]
    repair = state["messages"][-2].content
    assert 'Column "total" does not exist' in repair


def test_schema_error_kind_when_attempts_run_out():
    state = run(
        fake_llm(sql_reply("SELECT nope FROM public.orders")), FakeDataSource(), 1
    )

    assert state["error_kind"] == ErrorKind.SCHEMA


class PickyRetriever(SchemaRetriever):
    def __init__(self, **kwargs):
        super().__init__(search_above_tables=2, **kwargs)


def test_many_candidates_are_narrowed_by_the_llm():
    llm = fake_llm(
        "public.customers\npublic.orders",
        sql_reply("SELECT count(*) FROM public.orders"),
    )
    agent = AnalystAgent(
        llm,
        FakeDataSource(),
        retriever=PickyRetriever(),
        rewrite_question=False,
        select_above=2,
    )

    state = agent.run("customers orders items products", large_schema(3))

    assert state["selected"] == ["public.customers", "public.orders"]
    assert set(state["tables"]) == {"public.customers", "public.orders"}
    assert "telemetry" not in state["messages"][1].content


def test_unusable_selection_falls_back_to_top_candidates():
    llm = fake_llm("I am not sure", sql_reply("SELECT 1"))
    agent = AnalystAgent(
        llm,
        FakeDataSource(),
        retriever=PickyRetriever(),
        rewrite_question=False,
        select_above=1,
    )

    state = agent.run("customers orders", large_schema(3))

    assert state["selected"]
    assert state["result"] is not None


def test_missing_relation_heals_the_catalog_and_retries():
    fresh = DataSchema(
        tables=[
            Table(
                schema_name="public",
                name="orders",
                columns=[
                    Column(name="id", data_type="integer"),
                    Column(name="total", data_type="numeric"),
                ],
            )
        ]
    )
    healed_with = []

    def healer(tables):
        healed_with.append(tables)
        return fresh

    def execute(query):
        if query == "SELECT id FROM public.orders":
            raise QueryExecutionError('relation "public.orders" does not exist')
        return QueryResult(columns=["total"], rows=[[5]], row_count=1)

    datasource = FakeDataSource(execute=execute)
    llm = fake_llm(
        sql_reply("SELECT id FROM public.orders"),
        sql_reply("SELECT total FROM public.orders"),
    )
    agent = AnalystAgent(llm, datasource, healer=healer)

    state = agent.run("How many orders?", SCHEMA)

    assert healed_with == [["public.orders"]]
    assert state["healed"]
    assert state["result"].rows == [[5]]
    assert "total numeric" in state["messages"][-2].content
    assert "schema changed" in state["messages"][-2].content


def test_heal_happens_only_once():
    calls = []

    def healer(tables):
        calls.append(tables)
        return SCHEMA

    datasource = FakeDataSource(
        execute=lambda q: (_ for _ in ()).throw(
            QueryExecutionError('column "id" does not exist')
        )
    )
    llm = fake_llm(*[sql_reply("SELECT id FROM public.orders")] * 3)
    AnalystAgent(llm, datasource, healer=healer, max_attempts=3).run("q", SCHEMA)

    assert len(calls) == 1


def test_context_sections_reach_the_prompt():
    class Knowing(SchemaRetriever):
        def _signals(self, schema, question, terms):
            return Signals(
                value_hints=[
                    ValueHint(
                        term="paid",
                        table="public.orders",
                        column="id",
                        value="PAID",
                        score=0.9,
                    )
                ],
                glossary=[GlossaryTerm(term="revenue", definition="paid orders only")],
                examples=[
                    VerifiedQuery(
                        question="Orders?", sql="SELECT count(*) FROM public.orders"
                    )
                ],
            )

    agent = AnalystAgent(
        fake_llm(sql_reply("SELECT 1")), FakeDataSource(), retriever=Knowing()
    )
    prompt = agent.run("revenue from paid orders", SCHEMA)["messages"][1].content

    assert "Business terms:\n- revenue: paid orders only" in prompt
    assert "\"paid\" matches public.orders.id = 'PAID'" in prompt
    assert "Q: Orders?" in prompt
