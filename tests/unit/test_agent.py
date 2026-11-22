import pytest
from langchain_core.messages import HumanMessage

from datalyst.agent import AnalystAgent, ErrorKind
from datalyst.core.exceptions import LLMError
from tests.unit.fakes import FakeDataSource, failing_on, fake_llm, sql_reply

SCHEMA = "public.orders(id integer PK)"


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
    bad = "SELECT x FROM public.orders"
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
