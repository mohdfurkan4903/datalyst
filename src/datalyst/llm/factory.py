from typing import Any

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from datalyst.config import Settings
from datalyst.core.exceptions import ConfigurationError

OPENAI_COMPATIBLE_SERVERS = {
    "lmstudio": ("http://localhost:1234/v1", "lm-studio"),
    "vllm": ("http://localhost:8000/v1", "EMPTY"),
}


def create_chat_model(settings: Settings) -> BaseChatModel:
    provider = settings.llm_provider.strip().lower()
    api_key = settings.llm_api_key.get_secret_value() if settings.llm_api_key else None
    base_url = settings.llm_base_url or None

    if provider in OPENAI_COMPATIBLE_SERVERS:
        default_url, default_key = OPENAI_COMPATIBLE_SERVERS[provider]
        provider, base_url, api_key = (
            "openai",
            base_url or default_url,
            api_key or default_key,
        )

    kwargs = _provider_kwargs(provider, settings)
    if api_key:
        kwargs["api_key"] = api_key
    if base_url:
        kwargs["base_url"] = base_url

    try:
        return init_chat_model(settings.llm_model, model_provider=provider, **kwargs)
    except ImportError as e:
        raise ConfigurationError(
            f"LLM provider '{settings.llm_provider}' needs an extra package: {e}"
        ) from e
    except ValueError as e:
        raise ConfigurationError(f"Invalid LLM configuration: {e}") from e


def _provider_kwargs(provider: str, settings: Settings) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"temperature": settings.llm_temperature}
    if provider == "ollama":
        kwargs["client_kwargs"] = {"timeout": settings.llm_timeout_seconds}
    else:
        kwargs["timeout"] = settings.llm_timeout_seconds
        kwargs["max_retries"] = settings.llm_max_retries
    return kwargs
