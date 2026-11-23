import uuid
from datetime import date

import pytest

from datalyst.core.models import (
    Column,
    ColumnStats,
    QueryResult,
    SemanticType,
    Table,
    ValueCount,
)
from datalyst.enrichment.profiler import (
    name_tokens,
    profile_table,
    type_family,
    wants_value_index,
)


def profile_one(name, data_type, values, *, stats=None, rows=None, key=False, top=None):
    table = Table(
        schema_name="public",
        name="t",
        row_estimate=rows if rows is not None else len(values),
        columns=[Column(name=name, data_type=data_type)],
        primary_key=[name] if key else [],
    )
    sample = QueryResult(
        columns=[name], rows=[[v] for v in values], row_count=len(values)
    )
    return profile_table(table, stats or [], sample, {name: top} if top else None)[name]


def repeat(values, times=10):
    return [v for _ in range(times) for v in values]


@pytest.mark.parametrize(
    ("name", "data_type", "values", "expected"),
    [
        ("eml_addr", "text", ["a@x.io", "b@y.com", "c@z.org"], SemanticType.EMAIL),
        ("site", "text", ["https://a.io/x", "http://b.com"], SemanticType.URL),
        ("ref", "text", [str(uuid.uuid4()) for _ in range(5)], SemanticType.UUID),
        ("txn_id", "uuid", [uuid.uuid4() for _ in range(3)], SemanticType.UUID),
        ("ip", "text", ["10.0.0.1", "192.168.1.20"], SemanticType.IP_ADDRESS),
        (
            "ph_no",
            "varchar(20)",
            ["+1-555-0001", "+44 20 7946 0958"],
            SemanticType.PHONE,
        ),
        ("raw_amt", "text", ["12.50", "7", "-3.25"], SemanticType.NUMERIC_TEXT),
        ("is_dflt", "character(1)", repeat(["Y", "N"]), SemanticType.BOOLEAN_FLAG),
        (
            "ctry_cd",
            "character(2)",
            repeat(["IN", "US", "GB"]),
            SemanticType.COUNTRY_CODE,
        ),
        ("to_ccy", "character(3)", repeat(["USD", "INR"]), SemanticType.CURRENCY_CODE),
        ("body", "text", ["x" * 80, "y" * 90], SemanticType.FREE_TEXT),
        ("gw_resp", "jsonb", [{"code": "00"}], SemanticType.JSON),
        ("tot_amt_cents", "bigint", [100, 2500], SemanticType.MONEY_CENTS),
        ("unit_prc", "numeric(10,2)", [3.5, 9.99], SemanticType.MONEY),
        ("disc_pct", "numeric(5,2)", [10, 15], SemanticType.PERCENT),
        ("qty", "integer", [1, 2, 3], SemanticType.QUANTITY),
        ("cust_ref", "integer", [1, 2, 3], SemanticType.IDENTIFIER),
        ("pri", "smallint", repeat([1, 2, 3, 4], 10), SemanticType.CATEGORY),
        (
            "sts_cd",
            "character(1)",
            repeat(["O", "P", "S", "X"], 50),
            SemanticType.CATEGORY,
        ),
    ],
)
def test_semantic_types(name, data_type, values, expected):
    assert profile_one(name, data_type, values).semantic_type == expected


@pytest.mark.parametrize(
    ("values", "label"),
    [
        (["20240101", "20251120"], "YYYYMMDD"),
        (["2024-01-01", "2025-11-20"], "YYYY-MM-DD"),
        (["31/01/2024", "15/02/2024"], "DD/MM/YYYY"),
        (["01/02/2024", "03/04/2024"], "DD/MM/YYYY or MM/DD/YYYY"),
        (["2024-01-01 10:00:00", "2024-01-02T11:30"], "ISO 8601 timestamp"),
    ],
)
def test_dates_stored_as_text_report_their_format(values, label):
    profile = profile_one("ord_dt", "varchar(8)", values)

    assert profile.semantic_type == SemanticType.DATE_TEXT
    assert profile.format == label


def test_invalid_dates_are_not_dates():
    profile = profile_one("code", "text", ["20241399", "20240000"])

    assert profile.semantic_type == SemanticType.NUMERIC_TEXT


def test_text_date_range_uses_sorted_values():
    profile = profile_one("ord_dt", "varchar(8)", ["20240505", "20240101", "20251120"])

    assert (profile.min_value, profile.max_value) == ("20240101", "20251120")


def test_ranges_prefer_statistics_and_skip_identifiers():
    stats = [
        ColumnStats(column="crt_dt", histogram_bounds=["2021-01-01", "2023-09-26"])
    ]
    profile = profile_one("crt_dt", "date", [date(2022, 1, 1)], stats=stats, rows=500)

    assert (profile.min_value, profile.max_value) == ("2021-01-01", "2023-09-26")
    assert profile_one("cust_id", "integer", [1, 9]).min_value is None


def test_null_fraction_distinct_and_uniqueness():
    profile = profile_one("cust_id", "integer", [1, 2, None, 3], key=True)
    assert profile.null_fraction == 0.25
    assert profile.unique

    stats = [ColumnStats(column="eml", null_fraction=0.0, distinct=1000)]
    assert profile_one("eml", "text", ["a@x.io"], stats=stats, rows=1000).unique


def test_distinct_falls_back_to_sample_only_when_clearly_low():
    low = profile_one("sts", "text", repeat(["A", "B"], 100), rows=10_000)
    high = profile_one("nm", "text", [f"n{i}" for i in range(100)], rows=10_000)

    assert low.distinct_count == 2
    assert high.distinct_count is None


def test_top_values_from_statistics_when_not_counted():
    stats = [
        ColumnStats(
            column="sts",
            distinct=2,
            common_values=["A", "B"],
            common_frequencies=[0.75, 0.25],
        )
    ]
    profile = profile_one("sts", "text", ["A"], stats=stats, rows=100)

    assert profile.top_values == [
        ValueCount(value="A", count=75),
        ValueCount(value="B", count=25),
    ]


def test_counted_top_values_win():
    counted = [ValueCount(value="X", count=9)]
    profile = profile_one("sts", "text", ["X"], top=counted)

    assert profile.top_values == counted


def test_examples_are_distinct_and_short():
    profile = profile_one("note", "text", ["a", "a", "b" * 100, "c", "d", "e", "f"])

    assert profile.examples[0] == "a"
    assert len(profile.examples) == 5
    assert len(profile.examples[1]) == 40


def test_json_keys():
    profile = profile_one("payload", "jsonb", [{"code": 1, "auth": 2}, '{"avs": "Y"}'])

    assert profile.json_keys == ["code", "auth", "avs"]


def test_value_index_only_for_low_cardinality_values():
    def wants(name, data_type, values, rows=None):
        column = Column(name=name, data_type=data_type)
        return wants_value_index(
            column, profile_one(name, data_type, values, rows=rows)
        )

    assert wants("sts_cd", "text", repeat(["O", "P"], 50))
    assert not wants("eml", "text", ["a@x.io", "b@x.io"])
    assert not wants("created", "date", [date(2024, 1, 1)])
    assert not wants("nm", "text", [f"n{i}" for i in range(100)], rows=10_000)


def test_helpers():
    assert name_tokens("OrderItemID") == ["order", "item", "id"]
    assert name_tokens("mgr_emp_id") == ["mgr", "emp", "id"]
    assert type_family("character varying(8)") == "text"
    assert type_family("numeric(10,2)") == "decimal"
    assert type_family("timestamp with time zone") == "temporal"
    assert type_family("bigint") == "integer"
