"""Typed configuration. Every configurable value lives here and is listed in `.env.example`.

Later slices add their fields here; `tests/unit/core/test_settings.py` keeps `.env.example` in sync.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
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

    # --- UI session cookie (S22.11) ---
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
    llm_first_token_timeout_s: float = 15.0  # a stream with no first token by then is retried (0 = off)
    llm_max_retries: int = 2  # retries after the first attempt, retryable errors only
    llm_retry_base_s: float = 0.5  # backoff base when no retry-after is given
    llm_retry_max_s: float = 8.0
    # Per-model price overrides, USD per MTok, merged over app/llm/pricing.py defaults. JSON, e.g.
    # {"gpt-6.1-terra": {"input": 1, "output": 5, "cache_read": 0.05, "cache_write": 1.25}}
    llm_prices: dict[str, dict[str, float]] = Field(default_factory=dict)

    # --- upload / ingestion limits (design §6) ---
    max_upload_bytes: int = 25 * 1024 * 1024
    parse_timeout_s: int = 120
    embed_concurrency: int = 4  # concurrent embedding batches per document (ingest stage)
    chunker_version: Literal["chunker@1", "chunker@2", "chunker@2-overlap25"] = (
        "chunker@1"  # S22.01: default unchanged
    )
    brief_concurrency: int = 3  # concurrent brief map calls per document
    max_pages: int = 600  # PDFs above this end `unsupported` / `too_many_pages` before extraction
    parse_memory_mb: int = 2048  # address-space cap of the extraction subprocess (Linux only)

    # --- per-user rate limits: requests per window, Redis token bucket per (user, bucket) (design §13.4) ---
    rate_limit_chat_post_per_min: int = 20
    rate_limit_upload_per_min: int = 30
    rate_limit_default_per_min: int = 300
    rate_limit_window_s: int = 60  # the "min" above; tests widen it so refill cannot blur exact counts

    # --- daily LLM cost quota per user, UTC day; admins are unlimited (design §13.4) ---
    user_daily_usd: float = 1.00

    # --- document brief (design §9.1) ---
    brief_max_evidence_tokens: int = 12_000  # above this the brief maps over section groups, then merges
    brief_enabled: bool = True  # False skips the brief stage (eval uploads: no spend); version stays `ready`
    brief_max_usd: float = 0.05  # per document; reaching it stops further map calls (BRIEF_PARTIAL_BUDGET)

    # --- contextual chunk headers, index side only (design §6.5, S17.05); off until the A/B decides ---
    contextualize_variant: Literal["off", "deterministic", "llm"] = "off"
    contextualize_max_usd: float = 0.02  # per document; reaching it stops further header calls
    # S22.02 contextual index text for both legs: none = chunker text unchanged; deterministic = code header
    # (title, section, table caption/header, written by the chunk stage); llm = chunk_context@1 headers.
    index_context: Literal["none", "deterministic", "llm"] = "none"

    # --- retrieval config file under backend/config (design §7.3), e.g. retrieval@2 ---
    retrieval_config: str = "retrieval@2"

    # --- whole-document answering (design §8.2, S17.07) ---
    full_context_auto: bool = False  # off until the answer A/B decides (docs/evidence/answer_ab.md)
    full_context_max_docs: int = 3
    full_context_max_tokens: int = 50_000

    # --- fast path (design §8.2, S17.10): false = always run analyze_query (kill switch) ---
    chat_fast_path: bool = True
    chat_speculative_retrieval: bool = True  # retrieve on the raw question while `analyze` runs

    # --- plan-first answers (S22.04): answer_rag@4 + coverage plan; off until the A/B decides ---
    chat_plan_first: bool = False

    # --- support check (S22.06): 2 = support_check@2 (prunes unneeded citations, removes unsupported
    # segments, one regeneration if every segment fails); 1 until the paired A/B accepts it ---
    chat_support_version: Literal[1, 2] = 1
    # --- answer prompt: 7 = answer_rag@7 (completeness-first, scope rule; accepted by the paired dev A/B);
    # 3 = the previous short-answer wording; 6 = @7 without the scope rule ---
    chat_answer_version: Literal[3, 6, 7] = 7
    # --- analysis prompt (S22.09): 3 = analyze_query@3 (shorter prompt and output budget); 2 until A/B ---
    chat_analyze_version: Literal[2, 3] = 2
    # --- compare quotas + coverage gate (S22.05): answer_compare@2 + repair; off until the A/B decides ---
    chat_compare_quota: bool = False

    # --- scored abstention (S22.07): logistic combiner (config/abstain@1.yaml) replaces the binary gate;
    # off until calibrated on a v2 run and the paired A/B accepts it ---
    chat_abstain_scored: bool = False
    # --- typed calculation segments (S22.08): answer_rag@5, code computes numbers; off until A/B decides ---
    chat_calc: bool = False

    # --- routed locate loop for long filings (S22.10): bounded search/find/open steps when the first pass
    # over one long document is low-confidence; off until the paired A/B decides ---
    chat_locate: bool = False
    chat_locate_min_chunks: int = 150  # "long" = at least this many chunks ...
    chat_locate_min_pages: int = 60  # ... or at least this many pages

    # --- warm voice (S23.01): appends prompts/voice@1.md to the answer prompts; off until the A/B decides ---
    chat_voice: bool = False

    # --- clarify when ambiguous (S23.02): code-checked ambiguity gives option chips; off until reviewed ---
    chat_clarify: bool = False

    # --- follow-up memory: number of top chunks of the previous turn's evidence added to a follow-up's
    # own retrieval (0 = off); a rewritten follow-up often retrieves only the new subject ---
    chat_memory_carry_evidence: int = 0

    # --- telemetry (design §17, §20.1) ---
    otel_exporter_otlp_endpoint: str = "http://otel-lgtm:4317"
    otel_service_name: str = "vault-api"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    @field_validator("chat_support_version", "chat_analyze_version", "chat_answer_version", mode="before")
    @classmethod
    def _int_from_env(cls, v: object) -> object:
        """Env vars arrive as strings ("2"); the Literal[int] fields need the int."""
        return int(v) if isinstance(v, str) and v.strip().isdigit() else v

    @model_validator(mode="after")
    def _no_fake_in_prod(self) -> "Settings":
        if self.environment == "prod" and self.llm_provider == "fake":
            raise ValueError("llm_provider=fake is not allowed when environment=prod")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # required fields come from the environment
