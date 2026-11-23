import os
import warnings
from typing import Any

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from datalyst.config import Settings
from datalyst.core.exceptions import ConfigurationError

warnings.filterwarnings("ignore", message=".*uses fixed sampling defaults.*")

OPENAI_COMPATIBLE_SERVERS: dict[str, tuple[str, str | None]] = {
    "lmstudio": ("http://localhost:1234/v1", "lm-studio"),
    "vllm": ("http://localhost:8000/v1", "EMPTY"),
    "groq": ("https://api.groq.com/openai/v1", None),
}
KEY_ENV_VARS = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "google_genai": "GOOGLE_API_KEY",
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

    _require_key(settings.llm_provider, provider, api_key, base_url)
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
    except Exception as e:
        raise ConfigurationError(f"Invalid LLM configuration: {e}") from e


def _require_key(
    configured: str, provider: str, api_key: str | None, base_url: str | None
) -> None:
    variable = KEY_ENV_VARS.get(provider)
    if api_key or variable is None or os.getenv(variable):
        return
    if provider == "openai" and base_url and configured.strip().lower() != "groq":
        return
    raise ConfigurationError(
        f"No API key for the '{configured}' model. "
        "An admin can add one on the Settings page."
    )


def _provider_kwargs(provider: str, settings: Settings) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"temperature": settings.llm_temperature}
    if provider == "ollama":
        kwargs["client_kwargs"] = {"timeout": settings.llm_timeout_seconds}
    else:
        kwargs["timeout"] = settings.llm_timeout_seconds
        kwargs["max_retries"] = settings.llm_max_retries
    return kwargs
