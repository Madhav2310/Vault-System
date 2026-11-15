"""Typed configuration. Every configurable value lives here and is listed in `.env.example`.

Later slices add their fields here; `tests/unit/core/test_settings.py` keeps `.env.example` in sync.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,  # a validation error must never echo input values (they include API keys)
    )

    # --- environment ---
    environment: Literal["dev", "test", "prod"] = "dev"
    service_version: str = "dev"

    # --- infrastructure (required: no safe default) ---
    database_url: str = Field(description="postgresql+asyncpg://user:pass@host:5432/db")
    redis_url: str = Field(description="redis://host:6379/0")
    db_pool_size: int = 10
    db_max_overflow: int = 5

    # --- UI session cookie ---
    session_ttl_s: int = 28800  # lifetime of a /v1/session cookie
    local_autologin: bool = (
        True  # POST /v1/session/local: one-click sign-in on localhost only (never in prod)
    )
    session_secret: SecretStr | None = (
        None  # optional extra HMAC key; tokens are always bound to the user's key hash
    )

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

    # `fake` = scripted UI-test model (app/llm/fake.py); refused when environment is prod
    llm_provider: Literal["openai", "fake"] = "openai"

    # --- model roles (no date suffixes). Only app/llm/routing.py reads these. ---
    model_fast: str = "gpt-6-luna"
    model_balanced: str = "gpt-6.1-terra"
    model_deep: str = "gpt-6.1-sol"
    model_flagship: str = "gpt-6-astra"
    # Per-node overrides over the routing table, JSON, e.g.
    # {"analyze_query": {"role": "balanced", "effort": "low"},
    #  "generate:compare": {"role": "deep", "effort": "low"}}
    model_routes: dict[str, dict[str, str]] = Field(default_factory=dict)
    embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 1536

    # --- LLM gateway ---
    llm_timeout_s: float = 120.0
    llm_first_token_timeout_s: float = 15.0  # a stream with no first token by then is retried (0 = off)
    llm_max_retries: int = 2  # retries after the first attempt, retryable errors only
    llm_retry_base_s: float = 0.5  # backoff base when no retry-after is given
    llm_retry_max_s: float = 8.0
    llm_retry_after_max_s: float = 60.0  # a longer retry-after is not slept in process: the call raises
    # Per-model price overrides, USD per MTok, merged over app/llm/pricing.py defaults. JSON, e.g.
    # {"gpt-6.1-terra": {"input": 1, "output": 5, "cache_read": 0.05, "cache_write": 1.25}}
    llm_prices: dict[str, dict[str, float]] = Field(default_factory=dict)

    # --- upload / ingestion limits ---
    max_upload_bytes: int = 25 * 1024 * 1024
    parse_timeout_s: int = 120
    embed_concurrency: int = 4  # concurrent embedding batches per document (ingest stage)
    chunker_version: Literal["chunker@1", "chunker@2", "chunker@2-overlap25"] = (
        "chunker@1"  # default unchanged
    )
    brief_concurrency: int = 3  # concurrent brief map calls per document
    max_pages: int = 600  # PDFs above this end `unsupported` / `too_many_pages` before extraction
    parse_memory_mb: int = 2048  # address-space cap of the extraction subprocess (Linux only)

    # --- per-user rate limits: requests per window, Redis token bucket per (user, bucket) ---
    rate_limit_chat_post_per_min: int = 20
    rate_limit_upload_per_min: int = 30
    rate_limit_default_per_min: int = 300
    rate_limit_window_s: int = 60  # the "min" above; tests widen it so refill cannot blur exact counts

    # --- daily LLM cost quota per user, UTC day; admins are unlimited ---
    user_daily_usd: float = 1.00

    # --- document brief ---
    brief_max_evidence_tokens: int = 12_000  # above this the brief maps over section groups, then merges
    brief_enabled: bool = True  # False skips the brief stage (eval uploads: no spend); version stays `ready`
    brief_max_usd: float = 0.05  # per document; reaching it stops further map calls (BRIEF_PARTIAL_BUDGET)

    # --- contextual chunk headers, index side only; off until the A/B decides ---
    contextualize_variant: Literal["off", "deterministic", "llm"] = "off"
    contextualize_max_usd: float = 0.02  # per document; reaching it stops further header calls
    # contextual index text for both legs: none = chunker text unchanged; deterministic = code header
    # (title, section, table caption/header, written by the chunk stage); llm = chunk_context@1 headers.
    index_context: Literal["none", "deterministic", "llm"] = "none"

    # --- retrieval config file under backend/config, e.g. retrieval@2 ---
    retrieval_config: str = "retrieval@5"

    # --- whole-document answering, on request only: the token cap of the scope ---
    full_context_max_tokens: int = 50_000

    # --- fast path: false = always run analyze_query (kill switch) ---
    chat_fast_path: bool = True
    chat_speculative_retrieval: bool = True  # retrieve on the raw question while `analyze` runs

    # --- scored abstention: logistic combiner (config/abstain@1.yaml) replaces the binary gate;
    # off until calibrated on a v2 run and the paired A/B accepts it ---
    chat_abstain_scored: bool = False

    # --- routed locate loop for long filings: bounded search/find/open steps when the first pass
    # over one long document is low-confidence; off until the paired A/B decides ---
    chat_locate: bool = False
    chat_locate_min_chunks: int = 150  # "long" = at least this many chunks ...
    chat_locate_min_pages: int = 60  # ... or at least this many pages

    # --- clarify when ambiguous: code-checked ambiguity gives option chips; off until reviewed ---
    chat_clarify: bool = False

    # --- telemetry ---
    otel_exporter_otlp_endpoint: str = "http://otel-lgtm:4317"
    otel_service_name: str = "vault-api"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    @model_validator(mode="after")
    def _no_fake_in_prod(self) -> "Settings":
        if self.environment == "prod" and self.llm_provider == "fake":
            raise ValueError("llm_provider=fake is not allowed when environment=prod")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # required fields come from the environment
