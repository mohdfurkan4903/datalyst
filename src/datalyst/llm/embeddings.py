from typing import Any

from langchain.embeddings import init_embeddings
from langchain_core.embeddings import Embeddings

from datalyst.config import Settings
from datalyst.core.exceptions import ConfigurationError
from datalyst.llm.factory import OPENAI_COMPATIBLE_SERVERS

PROVIDER_KEY_ARGUMENT = {"google_genai": "google_api_key"}
NO_EMBEDDINGS = frozenset({"groq", "anthropic"})


def embedding_model_name(settings: Settings) -> str | None:
    if not settings.embeddings_provider or not settings.embeddings_model:
        return None
    return f"{settings.embeddings_provider.strip().lower()}:{settings.embeddings_model}"


def create_embeddings(settings: Settings) -> Embeddings | None:
    if not settings.embeddings_provider:
        return None
    provider = settings.embeddings_provider.strip().lower()
    if provider in NO_EMBEDDINGS:
        raise ConfigurationError(
            f"'{provider}' has no embedding models; pick another embedding provider."
        )
    if not settings.embeddings_model:
        raise ConfigurationError(
            "Choose an embedding model on the Settings page (embeddings.model)."
        )

    api_key = _secret(settings.embeddings_api_key) or (
        _secret(settings.llm_api_key)
        if provider == settings.llm_provider.strip().lower()
        else None
    )
    base_url = settings.embeddings_base_url or None
    kwargs: dict[str, Any] = {}
    if provider in OPENAI_COMPATIBLE_SERVERS:
        default_url, default_key = OPENAI_COMPATIBLE_SERVERS[provider]
        provider, base_url, api_key = (
            "openai",
            base_url or default_url,
            api_key or default_key,
        )
        kwargs["check_embedding_ctx_length"] = False
    if api_key:
        kwargs[PROVIDER_KEY_ARGUMENT.get(provider, "api_key")] = api_key
    if base_url:
        kwargs["base_url"] = base_url

    try:
        return init_embeddings(settings.embeddings_model, provider=provider, **kwargs)
    except ImportError as e:
        raise ConfigurationError(
            f"Embedding provider '{provider}' needs an extra package: {e}"
        ) from e
    except Exception as e:
        raise ConfigurationError(f"Invalid embedding configuration: {e}") from e


def _secret(value: Any) -> str | None:
    return value.get_secret_value() if value else None
