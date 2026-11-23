from datalyst.agent.prompts import (
    render_rewrite_prompt,
    render_schema,
    render_system_prompt,
)
from datalyst.core.models import Column, DataSchema, ForeignKey, Table


def test_render_schema_shows_keys_notes_and_values():
    schema = DataSchema(
        tables=[
            Table(
                schema_name="public",
                name="orders",
                description="One row per\ncheckout",
                columns=[
                    Column(name="id", data_type="integer"),
                    Column(name="customer_id", data_type="integer"),
                    Column(
                        name="status",
                        data_type="text",
                        description="Lifecycle state",
                        sample_values=["paid", "o'neil"],
                    ),
                ],
                primary_key=["id"],
                foreign_keys=[
                    ForeignKey(
                        column="customer_id",
                        references_table="public.customers",
                        references_column="id",
                    )
                ],
            )
        ]
    )

    assert render_schema(schema) == (
        "public.orders -- One row per checkout\n"
        "  id integer PK\n"
        "  customer_id integer -> public.customers.id\n"
        "  status text (values: 'paid', 'o''neil') -- Lifecycle state"
    )


def test_long_notes_are_truncated():
    table = Table(schema_name="s", name="t", description="x" * 500)
    rendered = render_schema(DataSchema(tables=[table]))

    assert rendered.endswith("...")
    assert len(rendered) < 320


def test_render_empty_schema():
    assert render_schema(DataSchema()) == "(no tables available)"


def test_system_prompt_mentions_dialect_and_refusal_marker():
    prompt = render_system_prompt("PostgreSQL", "SQL")
    assert "PostgreSQL" in prompt
    assert "CANNOT_ANSWER:" in prompt
    assert "{" not in prompt


def test_rewrite_prompt_defines_reply_format():
    prompt = render_rewrite_prompt("PostgreSQL")
    assert "QUESTION:" in prompt
    assert "TERMS:" in prompt
    assert "{" not in prompt


def test_semantic_hints():
    from datalyst.core.models import ColumnProfile, SemanticType

    table = Table(
        schema_name="public",
        name="ord_hdr",
        columns=[
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
                name="tot_amt_cents",
                data_type="bigint",
                profile=ColumnProfile(semantic_type=SemanticType.MONEY_CENTS),
            ),
            Column(
                name="created",
                data_type="timestamp with time zone",
                profile=ColumnProfile(
                    min_value="2024-01-01T00:00:00", max_value="2025-01-01T00:00:00"
                ),
            ),
        ],
    )
    rendered = render_schema(DataSchema(tables=[table]))

    assert "ord_dt varchar(8) [text date YYYYMMDD, 20240101..20251120]" in rendered
    assert "tot_amt_cents bigint [money in cents]" in rendered
    assert "created timestamp with time zone [2024-01-01..2025-01-01]" in rendered


def test_selection_parsing():
    from datalyst.agent.prompts import parse_selection

    candidates = ["public.ord_hdr", "public.cust_mstr", "sales.ord_hdr"]
    text = "1. public.ord_hdr\n- `cust_mstr`\n* ord_hdr\nNONE\nrandom words"

    assert parse_selection(text, candidates) == ["public.ord_hdr", "public.cust_mstr"]


def test_context_rendering_is_empty_without_knowledge():
    from datalyst.agent.prompts import render_context

    assert render_context() == ""
