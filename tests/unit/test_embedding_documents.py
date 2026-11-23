import pytest
from langchain_openai import OpenAIEmbeddings

from datalyst.config import Settings
from datalyst.core.exceptions import ConfigurationError
from datalyst.core.models import Column, ColumnProfile, DataSchema, SemanticType, Table
from datalyst.enrichment.embeddings import (
    build_documents,
    column_document,
    table_document,
    vector_literal,
)
from datalyst.llm import create_embeddings, embedding_model_name

TABLE = Table(
    schema_name="public",
    name="ord_hdr",
    description="One row per order.",
    synonyms=["orders", "sales"],
    example_questions=["Revenue by month?"],
    columns=[
        Column(
            name="sts_cd",
            data_type="character(1)",
            description="Order status",
            synonyms=["status"],
            sample_values=["S", "P"],
            profile=ColumnProfile(semantic_type=SemanticType.CATEGORY),
        ),
        Column(name="ord_no", data_type="bigint"),
    ],
)


def test_table_document():
    text = table_document(TABLE)

    assert text.splitlines()[0] == "Table public.ord_hdr"
    assert "Also called: orders, sales" in text
    assert "Columns: sts_cd (Order status), ord_no" in text
    assert "Questions: Revenue by month?" in text


def test_column_document():
    text = column_document(TABLE, TABLE.columns[0])

    assert text.startswith("Column public.ord_hdr.sts_cd (character(1))")
    assert "Kind: category" in text
    assert "Values: S, P" in text


def test_build_documents_uses_known_ids():
    ids = {("public", "ord_hdr", None): 1, ("public", "ord_hdr", "sts_cd"): 10}
    schema = DataSchema(tables=[TABLE])

    documents = build_documents(schema, ids)
    assert [(d.object_type, d.object_id) for d in documents] == [
        ("table", 1),
        ("column", 10),
    ]
    assert documents[0].content_hash != documents[1].content_hash
    assert len(build_documents(schema, ids, include_columns=False)) == 1


def test_vector_literal():
    assert vector_literal([0.1, -2.0, 3.141592653]) == "[0.1,-2,3.141593]"


def settings(**overrides):
    return Settings(**overrides)


def test_embeddings_are_optional():
    assert create_embeddings(settings()) is None
    assert embedding_model_name(settings()) is None


def test_providers_without_embeddings_are_rejected():
    with pytest.raises(ConfigurationError, match="no embedding models"):
        create_embeddings(settings(embeddings_provider="groq", embeddings_model="x"))


def test_local_server_embeddings_use_openai_protocol():
    model = create_embeddings(
        settings(embeddings_provider="lmstudio", embeddings_model="nomic-embed-text")
    )

    assert isinstance(model, OpenAIEmbeddings)
    assert model.openai_api_base == "http://localhost:1234/v1"
    assert (
        embedding_model_name(
            settings(
                embeddings_provider="LMStudio", embeddings_model="nomic-embed-text"
            )
        )
        == "lmstudio:nomic-embed-text"
    )


def test_describe_settings_fall_back_to_main_model():
    base = settings(llm_provider="groq", llm_model="a")
    assert base.describe_settings() is base

    split = settings(
        llm_provider="groq",
        llm_model="a",
        describe_provider="google_genai",
        describe_model="b",
    )
    assert (
        split.describe_settings().llm_provider,
        split.describe_settings().llm_model,
    ) == (
        "google_genai",
        "b",
    )
