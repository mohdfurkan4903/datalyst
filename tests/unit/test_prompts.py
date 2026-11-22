from datalyst.agent.prompts import render_schema, render_system_prompt
from datalyst.core.models import Column, DataSchema, ForeignKey, Table


def test_render_schema_shows_keys():
    schema = DataSchema(
        tables=[
            Table(
                schema_name="public",
                name="orders",
                columns=[
                    Column(name="id", data_type="integer"),
                    Column(name="customer_id", data_type="integer"),
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
        "public.orders(id integer PK, customer_id integer -> public.customers.id)"
    )


def test_render_empty_schema():
    assert render_schema(DataSchema()) == "(no tables available)"


def test_system_prompt_mentions_dialect_and_refusal_marker():
    prompt = render_system_prompt("PostgreSQL", "SQL")
    assert "PostgreSQL" in prompt
    assert "CANNOT_ANSWER:" in prompt
    assert "{" not in prompt
