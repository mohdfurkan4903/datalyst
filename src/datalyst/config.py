"""Configuration.

Two kinds of configuration, following the approach Airflow takes:

* ``BootSettings``: what a process needs before it can reach the metastore,
  mainly ``DATALYST_METASTORE_URL``, read from the environment or a ``.env``
  file.
* ``Settings``: everything else (LLM provider and keys, retrieval, sync, ...),
  stored in the metastore and edited by admins on the Settings page. Any option
  can be pinned with an environment variable ``DATALYST__<SECTION>__<KEY>``, which
  wins over the stored value and locks the option in the UI.

Effective value precedence: environment variable > metastore > default.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from datalyst.core.exceptions import ConfigurationError

ENV_PREFIX = "DATALYST__"


class BootSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DATALYST_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    metastore_url: str | None = None
    metastore_pool_min_size: int = Field(default=1, ge=0)
    metastore_pool_max_size: int = Field(default=10, ge=1)

    def require_metastore_url(self) -> str:
        if not self.metastore_url:
            raise ConfigurationError(
                "Set DATALYST_METASTORE_URL to the metastore's address."
            )
        return self.metastore_url


@lru_cache
def get_boot_settings() -> BootSettings:
    return BootSettings()


class Settings(BaseModel):
    """Runtime options. Field names are ``<section>_<key>`` of an ``Option``."""

    model_config = ConfigDict(extra="forbid")

    llm_provider: str = "openai"
    llm_model: str = "gpt-4.1-mini"
    llm_api_key: SecretStr | None = None
    llm_base_url: str | None = None
    llm_temperature: float = Field(default=0.0, ge=0, le=2)
    llm_timeout_seconds: float = Field(default=60, gt=0)
    llm_max_retries: int = Field(default=2, ge=0)

    describe_enabled: bool = True
    describe_sample_rows: int = Field(default=5, ge=0, le=20)
    describe_provider: str | None = None
    describe_model: str | None = None
    describe_api_key: SecretStr | None = None
    describe_base_url: str | None = None

    embeddings_provider: str | None = None
    embeddings_model: str | None = None
    embeddings_api_key: SecretStr | None = None
    embeddings_base_url: str | None = None
    embeddings_columns: bool = True

    agent_max_attempts: int = Field(default=3, ge=1)
    agent_rewrite_question: bool = True

    retrieval_search_above_tables: int = Field(default=30, ge=0)
    retrieval_top_k: int = Field(default=10, ge=1)
    retrieval_max_tables: int = Field(default=20, ge=1)
    retrieval_candidates: int = Field(default=30, ge=1)
    retrieval_select_above: int = Field(default=12, ge=1)

    query_timeout_seconds: float = Field(default=30, gt=0)
    query_max_rows: int = Field(default=1000, gt=0)
    query_pool_min_size: int = Field(default=1, ge=0)
    query_pool_max_size: int = Field(default=5, ge=1)
    query_sample_values: bool = True

    sync_batch_size: int = Field(default=200, ge=1)
    sync_profile_sample_rows: int = Field(default=500, ge=10)
    sync_profile_max_age_hours: float = Field(default=24, gt=0)
    sync_poll_interval_seconds: float = Field(default=2.0, gt=0)
    sync_lease_seconds: int = Field(default=900, ge=60)

    auth_token_minutes: int = Field(default=480, ge=5)

    def describe_settings(self) -> "Settings":
        """The settings for background descriptions: the describe model if set."""
        if not self.describe_provider:
            return self
        return self.model_copy(
            update={
                "llm_provider": self.describe_provider,
                "llm_model": self.describe_model or self.llm_model,
                "llm_api_key": self.describe_api_key,
                "llm_base_url": self.describe_base_url,
            }
        )


class OptionType(StrEnum):
    TEXT = "text"
    SECRET = "secret"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"


@dataclass(frozen=True)
class Option:
    section: str
    key: str
    description: str
    choices: tuple[str, ...] = ()

    @property
    def name(self) -> str:
        return f"{self.section}.{self.key}"

    @property
    def field(self) -> str:
        return f"{self.section}_{self.key}"

    @property
    def env_var(self) -> str:
        return f"{ENV_PREFIX}{self.section.upper()}__{self.key.upper()}"

    @property
    def type(self) -> OptionType:
        annotation = str(Settings.model_fields[self.field].annotation)
        for marker, kind in (
            ("SecretStr", OptionType.SECRET),
            ("bool", OptionType.BOOLEAN),
            ("int", OptionType.INTEGER),
            ("float", OptionType.NUMBER),
        ):
            if marker in annotation:
                return kind
        return OptionType.TEXT

    @property
    def secret(self) -> bool:
        return self.type == OptionType.SECRET

    @property
    def default(self) -> Any:
        return Settings.model_fields[self.field].default


LLM_PROVIDERS = (
    "openai",
    "anthropic",
    "google_genai",
    "groq",
    "ollama",
    "lmstudio",
    "vllm",
)
EMBEDDING_PROVIDERS = ("openai", "google_genai", "ollama")

SECTIONS: dict[str, str] = {
    "llm": "Model that answers questions",
    "describe": "Background table and column descriptions",
    "embeddings": "Meaning-based search",
    "agent": "Question answering",
    "retrieval": "Choosing the tables a question needs",
    "query": "Running queries on data sources",
    "sync": "Background worker",
    "auth": "Sign-in",
}

OPTIONS: tuple[Option, ...] = (
    Option("llm", "provider", "LLM provider.", LLM_PROVIDERS),
    Option(
        "llm", "model", "Model name, for example gpt-4.1-mini or claude-sonnet-5-5."
    ),
    Option("llm", "api_key", "API key for the provider. Not needed for local servers."),
    Option(
        "llm", "base_url", "Custom endpoint, for an OpenAI-compatible or local server."
    ),
    Option(
        "llm", "temperature", "Sampling temperature; 0 gives the most consistent SQL."
    ),
    Option("llm", "timeout_seconds", "Time limit for one model call."),
    Option("llm", "max_retries", "Retries for a failed model call."),
    Option(
        "describe", "enabled", "Write descriptions of tables and columns with the LLM."
    ),
    Option(
        "describe",
        "sample_rows",
        "Sample rows per table sent with a description request, for sources whose data policy allows rows.",
    ),
    Option(
        "describe",
        "provider",
        "A separate, cheaper provider for descriptions. Empty uses the question model.",
        LLM_PROVIDERS,
    ),
    Option(
        "describe", "model", "Model for descriptions. Empty uses the question model."
    ),
    Option("describe", "api_key", "API key for the description provider."),
    Option("describe", "base_url", "Custom endpoint for the description provider."),
    Option(
        "embeddings",
        "provider",
        "Embedding provider. Empty turns meaning-based search off; keyword search still works.",
        EMBEDDING_PROVIDERS,
    ),
    Option(
        "embeddings", "model", "Embedding model, for example text-embedding-3-small."
    ),
    Option(
        "embeddings",
        "api_key",
        "API key for embeddings. Empty reuses the LLM key when the provider is the same.",
    ),
    Option("embeddings", "base_url", "Custom endpoint for embeddings."),
    Option(
        "embeddings",
        "columns",
        "Embed every column as well as every table (better recall, more vectors).",
    ),
    Option(
        "agent", "max_attempts", "Attempts to write a working query before giving up."
    ),
    Option(
        "agent",
        "rewrite_question",
        "Rewrite follow-up questions into stand-alone ones before searching.",
    ),
    Option(
        "retrieval",
        "search_above_tables",
        "Search for relevant tables only when a source has more tables than this.",
    ),
    Option("retrieval", "top_k", "Tables sent to the model per question."),
    Option("retrieval", "max_tables", "Upper limit of tables after adding join paths."),
    Option("retrieval", "candidates", "Candidate tables considered by search."),
    Option(
        "retrieval",
        "select_above",
        "Let the model pick tables when there are more candidates than this.",
    ),
    Option(
        "query", "timeout_seconds", "Statement timeout for queries on data sources."
    ),
    Option("query", "max_rows", "Most rows returned by one query."),
    Option("query", "pool_min_size", "Connections kept open per data source."),
    Option("query", "pool_max_size", "Most connections per data source and process."),
    Option("query", "sample_values", "Show example values of columns to the model."),
    Option("sync", "batch_size", "Tables read from a data source in one batch."),
    Option("sync", "profile_sample_rows", "Rows sampled to profile a table."),
    Option("sync", "profile_max_age_hours", "Re-profile tables older than this."),
    Option(
        "sync", "poll_interval_seconds", "How often an idle worker checks for jobs."
    ),
    Option(
        "sync",
        "lease_seconds",
        "A job held longer than this by a stopped worker is retried.",
    ),
    Option("auth", "token_minutes", "How long a sign-in lasts."),
)
OPTIONS_BY_NAME: dict[str, Option] = {option.name: option for option in OPTIONS}

assert {o.field for o in OPTIONS} == set(Settings.model_fields), (
    "Options and Settings differ"
)


class Source(StrEnum):
    DEFAULT = "default"
    METASTORE = "metastore"
    ENVIRONMENT = "env"


@dataclass(frozen=True)
class Resolved:
    settings: Settings
    sources: dict[str, Source]


def option(name: str) -> Option:
    try:
        return OPTIONS_BY_NAME[name]
    except KeyError:
        raise ConfigurationError(f"Unknown setting '{name}'.") from None


def env_overrides(environ: Mapping[str, str]) -> dict[str, str]:
    """Settings pinned by ``DATALYST__SECTION__KEY`` variables, by option name."""
    found = {}
    for item in OPTIONS:
        value = environ.get(item.env_var)
        if value is not None:
            found[item.name] = value
    return found


def resolve(stored: Mapping[str, Any], environ: Mapping[str, str]) -> Resolved:
    """Combine defaults, stored values and environment overrides into Settings."""
    values: dict[str, Any] = {}
    sources = {item.name: Source.DEFAULT for item in OPTIONS}
    for name, value in stored.items():
        if name in OPTIONS_BY_NAME:
            values[OPTIONS_BY_NAME[name].field] = value
            sources[name] = Source.METASTORE
    for name, value in env_overrides(environ).items():
        values[OPTIONS_BY_NAME[name].field] = _blank_to_none(value)
        sources[name] = Source.ENVIRONMENT
    return Resolved(validate(values), sources)


def validate(values: Mapping[str, Any]) -> Settings:
    try:
        return Settings.model_validate(dict(values))
    except ValidationError as e:
        problems = "; ".join(
            f"{_option_name(error['loc'])}: {error['msg']}" for error in e.errors()
        )
        raise ConfigurationError(f"Invalid settings: {problems}") from e


def plain_value(settings: Settings, item: Option) -> Any:
    value = getattr(settings, item.field)
    if isinstance(value, SecretStr):
        return value.get_secret_value()
    return value


HIDDEN = "< hidden >"


def export_ini(
    resolved: Resolved,
    *,
    section: str | None = None,
    include_descriptions: bool = True,
    include_sources: bool = False,
    include_secrets: bool = False,
    boot: BootSettings | None = None,
) -> str:
    """Render settings in the ``airflow config list`` style."""
    lines: list[str] = []
    if boot is not None and section in (None, "core"):
        lines += ["[core]"]
        if include_descriptions:
            lines.append("# Set with the DATALYST_METASTORE_URL environment variable.")
        value = boot.metastore_url
        shown = _mask_url(value) if value and not include_secrets else _ini_value(value)
        lines.append(
            f"metastore_url = {shown}" + (" # source: env" if include_sources else "")
        )
        lines.append("")
    for name, title in SECTIONS.items():
        if section not in (None, name):
            continue
        lines.append(f"[{name}]")
        if include_descriptions:
            lines.append(f"# {title}")
        for item in OPTIONS:
            if item.section != name:
                continue
            if include_descriptions:
                lines.append(f"# {item.description}")
                lines.append(f"# Environment variable: {item.env_var}")
            value = plain_value(resolved.settings, item)
            shown = (
                HIDDEN
                if item.secret and value and not include_secrets
                else _ini_value(value)
            )
            suffix = (
                f" # source: {resolved.sources[item.name]}" if include_sources else ""
            )
            lines.append(f"{item.key} = {shown}{suffix}")
        lines.append("")
    if section is not None and not lines:
        raise ConfigurationError(f"Unknown section '{section}'.")
    return "\n".join(lines).rstrip() + "\n"


def export_env(resolved: Resolved, *, include_secrets: bool = False) -> str:
    """Render the non-default settings as environment variables (a .env file)."""
    lines = [
        "# Datalyst settings that differ from the defaults.",
        "# Setting any of these as an environment variable pins it and locks it in the UI.",
    ]
    for item in OPTIONS:
        if resolved.sources[item.name] == Source.DEFAULT:
            continue
        value = plain_value(resolved.settings, item)
        if item.secret and not include_secrets:
            lines.append(f"# {item.env_var}=  (secret, not exported)")
            continue
        lines.append(f"{item.env_var}={_ini_value(value)}")
    return "\n".join(lines) + "\n"


def _ini_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, SecretStr):
        value = value.get_secret_value()
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, dict)):
        return json.dumps(value)
    return str(value)


def _mask_url(url: str) -> str:
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(url)
    if parts.password is None:
        return url
    netloc = parts.netloc.replace(f":{parts.password}@", ":***@", 1)
    return urlunsplit(parts._replace(netloc=netloc))


def _blank_to_none(value: str) -> str | None:
    return value if value.strip() else None


def _option_name(loc: tuple) -> str:
    field = str(loc[0]) if loc else ""
    for item in OPTIONS:
        if item.field == field:
            return item.name
    return field
