import pytest
from langchain_core.messages import AIMessage

from datalyst.agent.parsing import (
    extract_query,
    extract_refusal,
    message_text,
    parse_rewrite,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("```sql\nSELECT 1\n```", "SELECT 1"),
        ("```SQL\nSELECT 1\n```", "SELECT 1"),
        ("```\nSELECT 1\n```", "SELECT 1"),
        ("```sql SELECT 1```", "SELECT 1"),
        ("Here you go:\n```sql\nSELECT 1\n```\nHope it helps", "SELECT 1"),
        ("SELECT 1", "SELECT 1"),
        ("```sql\nSELECT 1\n```\n```sql\nSELECT 2\n```", "SELECT 1"),
    ],
)
def test_extract_query(text, expected):
    assert extract_query(text) == expected


def test_message_text_strips_reasoning_blocks():
    message = AIMessage("<think>let me think</think>\n```sql\nSELECT 1\n```")
    assert message_text(message) == "```sql\nSELECT 1\n```"


def test_message_text_joins_content_blocks():
    message = AIMessage(
        content=[{"type": "text", "text": "SELECT "}, {"type": "text", "text": "1"}]
    )
    assert message_text(message) == "SELECT 1"


def test_extract_refusal():
    assert extract_refusal("CANNOT_ANSWER: no such table") == "no such table"
    assert extract_refusal("CANNOT_ANSWER:") == "the question cannot be answered"
    assert extract_refusal("SELECT 1") is None
    assert extract_refusal("```sql\nSELECT 'CANNOT_ANSWER:'\n```") is None


def test_extract_refusal_inside_code_block():
    text = "```sql\nCANNOT_ANSWER: read-only access only\n```"
    assert extract_refusal(text) == "read-only access only"


def test_parse_rewrite():
    rewrite = parse_rewrite(
        "QUESTION: Total revenue per month\nTERMS: revenue, orders; amount\n, month"
    )
    assert rewrite.question == "Total revenue per month"
    assert rewrite.terms == ["revenue", "orders", "amount"]


def test_parse_rewrite_tolerates_markdown_and_case():
    rewrite = parse_rewrite(
        "**Question**: Clients by country\n- terms: client, country"
    )
    assert rewrite.question == "Clients by country"
    assert rewrite.terms == ["client", "country"]


def test_parse_rewrite_without_expected_format():
    rewrite = parse_rewrite("I am not sure what you mean")
    assert rewrite.question is None
    assert rewrite.terms == []
