"""Typed configuration. Every configurable value lives here and is listed in `.env.example`.

Later slices add their fields here; `tests/unit/core/test_settings.py` keeps `.env.example` in sync.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), env_file_encoding="utf-8", extra="ignore")

    # --- environment ---
    environment: Literal["dev", "test", "prod"] = "dev"
    service_version: str = "dev"

    # --- infrastructure (required: no safe default) ---
    database_url: str = Field(description="postgresql+asyncpg://user:pass@host:5432/db")
    redis_url: str = Field(description="redis://host:6379/0")
    db_pool_size: int = 10
    db_max_overflow: int = 5

    # --- object store ---
    object_store_backend: Literal["local", "s3"] = "local"
    object_store_root: str = "/data/blobs"

    # --- reranker service ---
    reranker_url: str = "http://reranker:8080"
    reranker_timeout_ms: int = 800

    # --- providers (optional at boot; /readyz reports what's missing) ---
    anthropic_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    deepseek_api_key: SecretStr | None = None

    # --- model roles (design §4.1; no date suffixes). Only app/llm/routing.py reads these. ---
    model_fast: str = "gpt-6-luna"
    model_balanced: str = "gpt-6.1-terra"
    model_deep: str = "gpt-6.1-sol"
    model_flagship: str = "gpt-6-astra"
    # Per-node overrides over the routing table (design §4.2), JSON, e.g.
    # {"analyze_query": {"role": "balanced", "effort": "low"},
    #  "generate:compare": {"role": "deep", "effort": "low"}}
    model_routes: dict[str, dict[str, str]] = Field(default_factory=dict)
    embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 1536

    # --- LLM gateway (design §5.3, §15) ---
    llm_timeout_s: float = 120.0
    llm_max_retries: int = 2  # retries after the first attempt, retryable errors only
    llm_retry_base_s: float = 0.5  # backoff base when no retry-after is given
    llm_retry_max_s: float = 8.0
    # Per-model price overrides, USD per MTok, merged over app/llm/pricing.py defaults. JSON, e.g.
    # {"gpt-6.1-terra": {"input": 1, "output": 5, "cache_read": 0.05, "cache_write": 1.25}}
    llm_prices: dict[str, dict[str, float]] = Field(default_factory=dict)

    # --- upload / ingestion limits (design §6) ---
    max_upload_bytes: int = 25 * 1024 * 1024
    parse_timeout_s: int = 120

    # --- telemetry (design §17, §20.1) ---
    otel_exporter_otlp_endpoint: str = "http://otel-lgtm:4317"
    otel_service_name: str = "vault-api"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # required fields come from the environment
