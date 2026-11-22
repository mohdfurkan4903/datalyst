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

    database_url: str
    include_schemas: list[str] = Field(default_factory=list)
    query_timeout_seconds: float = Field(default=30, gt=0)
    max_rows: int = Field(default=1000, gt=0)
    pool_min_size: int = Field(default=1, ge=0)
    pool_max_size: int = Field(default=5, ge=1)

    llm_provider: str = "openai"
    llm_model: str = "gpt-4.1-mini"
    llm_api_key: SecretStr | None = None
    llm_base_url: str | None = None
    llm_temperature: float = Field(default=0.0, ge=0, le=2)
    llm_timeout_seconds: float = Field(default=60, gt=0)
    llm_max_retries: int = Field(default=2, ge=0)

    agent_max_attempts: int = Field(default=3, ge=1)


@lru_cache
def get_settings() -> Settings:
    return Settings()
