import pytest
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI

from datalyst.config import Settings
from datalyst.core.exceptions import ConfigurationError
from datalyst.llm import create_chat_model


def settings(**overrides) -> Settings:
    return Settings(**overrides)


def test_openai_with_key():
    model = create_chat_model(settings(llm_provider="openai", llm_api_key="sk-test"))

    assert isinstance(model, ChatOpenAI)
    assert model.openai_api_key.get_secret_value() == "sk-test"
    assert model.max_retries == 2


def test_lmstudio_is_openai_compatible_with_local_defaults():
    model = create_chat_model(settings(llm_provider="lmstudio", llm_model="qwen"))

    assert isinstance(model, ChatOpenAI)
    assert model.openai_api_base == "http://localhost:1234/v1"


def test_groq_uses_its_openai_compatible_endpoint():
    model = create_chat_model(
        settings(llm_provider="groq", llm_model="openai/gpt-oss-20b", llm_api_key="gsk")
    )

    assert isinstance(model, ChatOpenAI)
    assert model.openai_api_base == "https://api.groq.com/openai/v1"
    assert model.openai_api_key.get_secret_value() == "gsk"


def test_custom_base_url_overrides_default():
    model = create_chat_model(
        settings(llm_provider="lmstudio", llm_base_url="http://gpu-box:1234/v1")
    )
    assert model.openai_api_base == "http://gpu-box:1234/v1"


def test_ollama():
    model = create_chat_model(
        settings(
            llm_provider="ollama",
            llm_model="llama3",
            llm_base_url="http://localhost:11434",
        )
    )

    assert isinstance(model, ChatOllama)
    assert model.base_url == "http://localhost:11434"


def test_unknown_provider_is_a_configuration_error():
    with pytest.raises(ConfigurationError):
        create_chat_model(settings(llm_provider="not-a-provider"))
