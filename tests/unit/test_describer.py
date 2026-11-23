import json

import pytest

from datalyst.core.exceptions import LLMError
from datalyst.core.models import (
    Column,
    ColumnProfile,
    ForeignKey,
    LLMDataPolicy,
    QueryResult,
    SemanticType,
    Table,
    ValueCount,
)
from datalyst.enrichment.describer import (
    COLUMNS_PER_CALL,
    describe,
    description_key,
    parse_description,
    render_evidence,
)
from tests.unit.fakes import RecordingLLM


def orders():
    return Table(
        schema_name="public",
        name="ord_hdr",
        row_estimate=6000,
        primary_key=["ord_no"],
        foreign_keys=[
            ForeignKey(
                column="cust_ref",
                references_table="public.cust_mstr",
                references_column="cust_id",
            )
        ],
        columns=[
            Column(name="ord_no", data_type="bigint"),
            Column(name="cust_ref", data_type="integer"),
            Column(
                name="ord_dt",
                data_type="varchar(8)",
                profile=ColumnProfile(
                    semantic_type=SemanticType.DATE_TEXT,
                    format="YYYYMMDD",
                    min_value="20240101",
                    max_value="20251120",
                ),
            ),
            Column(
                name="sts_cd",
                data_type="character(1)",
                sample_values=["S", "P"],
                profile=ColumnProfile(
                    semantic_type=SemanticType.CATEGORY,
                    distinct_count=2,
                    top_values=[
                        ValueCount(value="S", count=3),
                        ValueCount(value="P", count=1),
                    ],
                ),
            ),
        ],
    )


SAMPLE = QueryResult(
    columns=["ord_no", "cust_ref", "ord_dt", "sts_cd"],
    rows=[[100001, 7, "20240105", "S"]],
    row_count=1,
)


def reply(columns=("ord_no", "cust_ref", "ord_dt", "sts_cd"), **extra):
    return json.dumps(
        {
            "description": "One row per order.",
            "synonyms": ["orders", "orders"],
            "example_questions": ["Revenue by month?"],
            "columns": {
                c: {"description": f"About {c}", "synonyms": [c]} for c in columns
            },
            **extra,
        }
    )


def test_rows_policy_shows_everything():
    text = render_evidence(
        orders(),
        orders().columns,
        ["public.ord_dtl.ord_no"],
        LLMDataPolicy.ROWS,
        SAMPLE,
    )

    assert "Table: public.ord_hdr (table, about 6000 rows)" in text
    assert "ord_dt varchar(8), date text YYYYMMDD, range 20240101..20251120" in text
    assert "values: S (75%), P (25%)" in text
    assert "cust_ref integer, joins public.cust_mstr.cust_id" in text
    assert "Referenced by: public.ord_dtl.ord_no" in text
    assert "100001 | 7 | 20240105 | S" in text


def test_stats_policy_hides_rows():
    text = render_evidence(orders(), orders().columns, [], LLMDataPolicy.STATS, SAMPLE)

    assert "values: S (75%)" in text
    assert "Sample rows" not in text
    assert "100001" not in text


def test_names_policy_sends_no_data():
    text = render_evidence(orders(), orders().columns, [], LLMDataPolicy.NAMES, SAMPLE)

    assert "date text YYYYMMDD" in text
    assert "20240101" not in text
    assert "S (75%)" not in text
    assert "Sample rows" not in text


@pytest.mark.parametrize(
    "wrapper",
    [
        "{}",
        "```json\n{}\n```",
        "<think>let me look</think>Here you go: {} Done.",
    ],
)
def test_parse_tolerates_wrapping(wrapper):
    parsed = parse_description(
        wrapper.replace("{}", reply()), ["ord_no", "cust_ref", "ord_dt", "sts_cd"]
    )

    assert parsed.description == "One row per order."
    assert parsed.synonyms == ["orders"]
    assert set(parsed.columns) == {"ord_no", "cust_ref", "ord_dt", "sts_cd"}


def test_parse_keeps_known_columns_only_and_accepts_plain_strings():
    text = json.dumps(
        {
            "description": "x",
            "columns": {
                "ORD_NO": "The order number",
                "made_up": "nope",
                "sts_cd": {"description": " "},
            },
        }
    )
    parsed = parse_description(text, ["ord_no", "sts_cd"])

    assert set(parsed.columns) == {"ord_no"}
    assert parsed.columns["ord_no"].description == "The order number"


@pytest.mark.parametrize(
    "text",
    [
        "no json here",
        "{not json}",
        json.dumps({"description": ""}),
        json.dumps({"columns": {}}),
    ],
)
def test_parse_rejects_unusable_replies(text):
    with pytest.raises(ValueError):
        parse_description(text, ["ord_no"])


def test_parse_clips_long_text():
    text = json.dumps({"description": "word " * 200, "synonyms": ["x" * 100, "ok"]})
    parsed = parse_description(text, [])

    assert len(parsed.description) <= 400
    assert parsed.synonyms == ["ok"]


def test_describe_and_record_usage():
    llm = RecordingLLM(reply())

    description, usage = describe(
        llm, orders(), [], LLMDataPolicy.ROWS, SAMPLE, "test:model"
    )

    assert description.columns["sts_cd"].description == "About sts_cd"
    assert usage[0].model == "test:model"
    assert usage[0].input_tokens == 10
    assert "Sample rows" in llm.prompts[0]


def test_describe_retries_once_after_a_bad_reply():
    llm = RecordingLLM("sorry, no", reply())

    description, usage = describe(llm, orders(), [], LLMDataPolicy.NAMES)

    assert description.description == "One row per order."
    assert len(usage) == 2
    assert "could not be used" in llm.prompts[1]


def test_describe_gives_up_after_two_bad_replies():
    with pytest.raises(LLMError, match="Unusable"):
        describe(RecordingLLM("nope", "still nope"), orders(), [], LLMDataPolicy.NAMES)


def test_skipped_columns_are_requested_again():
    llm = RecordingLLM(
        reply(columns=("ord_no", "cust_ref")), reply(columns=("ord_dt", "sts_cd"))
    )

    description, _ = describe(llm, orders(), [], LLMDataPolicy.NAMES)

    assert set(description.columns) == {"ord_no", "cust_ref", "ord_dt", "sts_cd"}
    assert "skipped these columns: ord_dt, sts_cd" in llm.prompts[1]


def test_wide_tables_are_described_in_chunks():
    wide = Table(
        schema_name="public",
        name="wide",
        columns=[
            Column(name=f"c{i}", data_type="text") for i in range(COLUMNS_PER_CALL + 5)
        ],
    )
    first = [f"c{i}" for i in range(COLUMNS_PER_CALL)]
    second = [f"c{i}" for i in range(COLUMNS_PER_CALL, COLUMNS_PER_CALL + 5)]
    llm = RecordingLLM(reply(columns=first), reply(columns=second))

    description, usage = describe(llm, wide, [], LLMDataPolicy.NAMES)

    assert len(description.columns) == COLUMNS_PER_CALL + 5
    assert len(usage) == 2


def test_description_key_tracks_meaningful_changes_only():
    base = description_key(orders(), [], LLMDataPolicy.ROWS, "m")

    moved_range = orders()
    moved_range.columns[2].profile.max_value = "20261231"
    assert description_key(moved_range, [], LLMDataPolicy.ROWS, "m") == base

    renamed = orders()
    renamed.columns[0].name = "order_no"
    assert description_key(renamed, [], LLMDataPolicy.ROWS, "m") != base
    assert description_key(orders(), ["public.x.y"], LLMDataPolicy.ROWS, "m") != base
    assert description_key(orders(), [], LLMDataPolicy.NAMES, "m") != base
    assert description_key(orders(), [], LLMDataPolicy.ROWS, "other") != base
