from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DATALYST_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    metastore_url: str
    metastore_pool_min_size: int = Field(default=1, ge=0)
    metastore_pool_max_size: int = Field(default=10, ge=1)
    secret_key: SecretStr | None = None

    auth_token_minutes: int = Field(default=480, ge=5)
    api_cors_origins: list[str] = Field(default_factory=list)

    query_timeout_seconds: float = Field(default=30, gt=0)
    max_rows: int = Field(default=1000, gt=0)
    pool_min_size: int = Field(default=1, ge=0)
    pool_max_size: int = Field(default=5, ge=1)

    schema_sample_values: bool = True

    sync_batch_size: int = Field(default=200, ge=1)
    profile_sample_rows: int = Field(default=500, ge=10)
    profile_max_age_hours: float = Field(default=24, gt=0)
    worker_poll_interval_seconds: float = Field(default=2.0, gt=0)
    worker_lease_seconds: int = Field(default=900, ge=60)

    retrieval_search_above_tables: int = Field(default=30, ge=0)
    retrieval_top_k: int = Field(default=10, ge=1)
    retrieval_max_tables: int = Field(default=20, ge=1)
    retrieval_rewrite_question: bool = True
    retrieval_candidates: int = Field(default=30, ge=1)
    retrieval_select_above: int = Field(default=12, ge=1)

    llm_provider: str = "openai"
    llm_model: str = "gpt-4.1-mini"
    llm_api_key: SecretStr | None = None
    llm_base_url: str | None = None
    llm_temperature: float = Field(default=0.0, ge=0, le=2)
    llm_timeout_seconds: float = Field(default=60, gt=0)
    llm_max_retries: int = Field(default=2, ge=0)

    agent_max_attempts: int = Field(default=3, ge=1)

    describe_enabled: bool = True
    describe_sample_rows: int = Field(default=5, ge=0, le=20)
    describe_llm_provider: str | None = None
    describe_llm_model: str | None = None
    describe_llm_api_key: SecretStr | None = None
    describe_llm_base_url: str | None = None

    def describe_settings(self) -> "Settings":
        if not self.describe_llm_provider:
            return self
        return self.model_copy(
            update={
                "llm_provider": self.describe_llm_provider,
                "llm_model": self.describe_llm_model or self.llm_model,
                "llm_api_key": self.describe_llm_api_key,
                "llm_base_url": self.describe_llm_base_url,
            }
        )

    embedding_provider: str | None = None
    embedding_model: str | None = None
    embedding_api_key: SecretStr | None = None
    embedding_base_url: str | None = None
    embed_columns: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
