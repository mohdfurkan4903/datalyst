import pytest

from datalyst.agent import AnalystAgent
from datalyst.agent.memory import (
    mentions_memory,
    parse_memory_update,
    render_conversation,
    render_memories,
)
from datalyst.catalog import SchemaRetriever
from datalyst.core.models import Memory, MemoryOrigin, Turn
from tests.unit.fakes import FakeDataSource, fake_llm, large_schema, sql_reply
from tests.unit.test_agent import SCHEMA


@pytest.mark.parametrize(
    "question",
    [
        "Remember that revenue means paid orders",
        "from now on show amounts in EUR",
        "Always exclude test customers",
        "I'm in the EMEA sales team, how many orders did we get?",
        "our fiscal year starts in April",
        "By default, group by month",
        "forget the thing about EUR",
        "We call cancelled orders churn",
        "my region is Germany",
    ],
)
def test_messages_that_state_something_lasting_are_checked(question):
    assert mentions_memory(question)


@pytest.mark.parametrize(
    "question",
    [
        "How many orders were placed last month?",
        "Top 5 customers by revenue",
        "and for Germany?",
        "What is the average order value by country?",
    ],
)
def test_ordinary_questions_cost_nothing_extra(question):
    assert not mentions_memory(question)


def test_parses_remember_and_forget_lines():
    reply = (
        "REMEMBER (all): Show amounts in EUR.\n"
        "- REMEMBER (source): Revenue means paid and shipped orders\n"
        "FORGET: 2\n"
        "FORGET: 99\n"
        "NO_QUESTION\n"
    )

    update = parse_memory_update(reply, valid_ids=[1, 2])

    assert [(m.text, m.all_sources) for m in update.remember] == [
        ("Show amounts in EUR.", True),
        ("Revenue means paid and shipped orders", False),
    ]
    assert update.forget == [2]
    assert update.only_instruction is True


def test_nothing_to_remember():
    update = parse_memory_update("NOTHING")

    assert update.empty and not update.only_instruction


def test_secrets_and_long_or_duplicate_statements_are_never_kept():
    reply = (
        "REMEMBER (all): my password is hunter2\n"
        "REMEMBER (all): the api key is sk-123\n"
        "REMEMBER (all): card 4111111111111111\n"
        f"REMEMBER (all): {'x' * 250}\n"
        "REMEMBER (all): Fiscal year starts in April\n"
        "REMEMBER (all): fiscal year starts in april\n"
    )

    update = parse_memory_update(reply)

    assert [m.text for m in update.remember] == ["Fiscal year starts in April"]


def test_chat_and_memories_are_rendered_for_the_model():
    turns = [
        Turn(
            question="Top customers?",
            query="SELECT name,\n  SUM(total) FROM orders GROUP BY 1",
            row_count=5,
            preview="columns ['name']: [[\"Ann\"]]",
        ),
        Turn(question="Delete everything", declined="read-only access only"),
    ]

    text = render_conversation(turns)

    assert text.startswith("Conversation so far (oldest first):")
    assert "1. Question: Top customers?" in text
    assert "Query: SELECT name, SUM(total) FROM orders GROUP BY 1" in text
    assert "Result: 5 row(s), for example columns" in text
    assert "Not answered: read-only access only" in text
    assert render_conversation([]) == ""
    assert render_memories([]) == ""
    assert render_memories(["a", "b"]) == (
        "About this user (saved from earlier chats):\n- a\n- b"
    )
    assert len(render_memories(["x" * 1500] * 10)) < 4500


def test_follow_up_prompt_carries_the_chat_and_the_memories():
    agent = AnalystAgent(
        fake_llm(sql_reply("SELECT count(*) FROM public.orders WHERE true")),
        FakeDataSource(),
    )
    turns = [
        Turn(
            question="How many orders?",
            query="SELECT count(*) FROM public.orders",
            row_count=1,
        )
    ]

    state = agent.run(
        "and only the paid ones?",
        SCHEMA,
        turns,
        ["Revenue means paid orders"],
    )

    prompt = state["messages"][1].content
    assert "Conversation so far" in prompt
    assert "Query: SELECT count(*) FROM public.orders" in prompt
    assert "About this user" in prompt and "Revenue means paid orders" in prompt
    assert prompt.rstrip().endswith("Question: and only the paid ones?")
    assert prompt.index("About this user") < prompt.index("Conversation so far")
    system = state["messages"][0].content
    assert "Conversation so far" in system and "About this user" in system


def test_without_memory_the_prompt_is_unchanged():
    agent = AnalystAgent(fake_llm(sql_reply("SELECT 1")), FakeDataSource())

    state = agent.run("How many orders?", SCHEMA)

    prompt = state["messages"][1].content
    assert "Conversation so far:" not in prompt and "About this user (" not in prompt


def test_rewrite_sees_the_chat_so_a_follow_up_finds_the_right_tables():
    llm = fake_llm(
        "QUESTION: Which customers bought electronics, only in Germany?\n"
        "TERMS: customers, orders, products, electronics, country",
        sql_reply("SELECT 1"),
    )
    agent = AnalystAgent(llm, FakeDataSource(), retriever=SchemaRetriever())
    turns = [
        Turn(
            question="Which customers bought electronics?",
            query="SELECT 1",
            row_count=3,
        )
    ]

    state = agent.run("and only in Germany?", large_schema(), turns)

    assert state["rewritten_question"].endswith("only in Germany?")
    assert "public.customers" in state["tables"]


def test_asking_the_model_what_to_remember():
    reply = "REMEMBER (all): Show amounts in EUR\nNO_QUESTION"
    agent = AnalystAgent(fake_llm(reply), FakeDataSource())
    existing = [
        Memory(id=4, user_id=1, text="Old fact", origin=MemoryOrigin.USER),
    ]

    update = agent.extract_memory("from now on show amounts in EUR", existing)

    assert [m.text for m in update.remember] == ["Show amounts in EUR"]
    assert update.only_instruction
