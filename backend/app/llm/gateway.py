"""LLM gateway (design §5.3): the only path to a model. Per call, in order:

0. resolve node → role → model + effort through the router (design §4.2)
1. rate-limit tokens (lane-aware; stub until S5)
2. charge the turn budget
3. one OTel span per attempt (IDs/counters only)
4. provider call with typed errors
5. retry with jitter, honoring retry-after (retryable errors only)
6. update the circuit breaker (stub until S5)
7. one `llm_calls` row per attempt (own short DB session)
8. stop_reason checks, then schema validation
9. after retries on a retryable error: one attempt on the role's fallback (`DEGRADED_MODEL_FALLBACK`)

No DB connection is held across a provider call: each ledger row is written in its own short session after the
attempt finishes. No prompt, input or completion text reaches spans, logs or exception messages.
"""

import asyncio
import json
import random
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import aclosing
from dataclasses import dataclass, replace
from decimal import Decimal
from functools import lru_cache
from typing import Any, Protocol, TypeVar, overload

from opentelemetry import trace
from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.ids import uuid7
from app.core.settings import Settings, get_settings
from app.db.models.ledger import LlmCall
from app.llm.errors import (
    CircuitOpenError,
    LlmError,
    ProviderConnectionError,
    ProviderError,
    RateLimitedError,
    RefusalError,
    RequestConflictError,
    SchemaValidationError,
    TruncatedError,
)
from app.llm.pricing import PriceTable, price_table_from_settings
from app.llm.prompts import Prompt, PromptRegistry, default_registry
from app.llm.providers import ChatProvider, EmbeddingProvider
from app.llm.routing import DEGRADED_MODEL_FALLBACK, ModelRouter, Resolved, Route
from app.llm.types import (
    ChatRequest,
    ChatResponse,
    CitationDelta,
    EmbeddingResponse,
    Final,
    Lane,
    ProviderFinal,
    SearchResult,
    TextDelta,
    TextPart,
    Usage,
)

T = TypeVar("T", bound=BaseModel)
R = TypeVar("R")

MAX_EMBED_BATCH = 128  # design §4
TRUNCATING_STOPS = frozenset({"max_tokens", "model_context_window_exceeded"})
_tracer = trace.get_tracer("app.llm.gateway")


# --- collaborators: budget, rate limiter, circuit breaker (S5 fills the latter two) ------------------


class Budget(Protocol):
    """The turn budget (design §5.2 TurnBudget). `deadline_at` is on the `time.monotonic()` clock."""

    @property
    def deadline_at(self) -> float | None: ...

    def charge_call(self) -> None:
        """Raise BudgetExceededError when no LLM call is left."""

    def record_usage(self, usage: Usage) -> None: ...


class RateLimiter(Protocol):
    async def acquire(self, *, model: str, lane: Lane, est_input_tokens: int) -> None: ...

    def reconcile(self, *, model: str, usage: Usage) -> None: ...

    def on_rate_limited(self, *, model: str, retry_after: float | None) -> None: ...


class NoopRateLimiter:
    async def acquire(self, *, model: str, lane: Lane, est_input_tokens: int) -> None:
        return None

    def reconcile(self, *, model: str, usage: Usage) -> None:
        return None

    def on_rate_limited(self, *, model: str, retry_after: float | None) -> None:
        return None


class CircuitBreaker(Protocol):
    def allow(self, model: str) -> bool: ...

    def record_success(self, model: str) -> None: ...

    def record_failure(self, model: str, error_type: str) -> None: ...


class NoopCircuitBreaker:
    def allow(self, model: str) -> bool:
        return True

    def record_success(self, model: str) -> None:
        return None

    def record_failure(self, model: str, error_type: str) -> None:
        return None


# --- ledger -----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class LlmCallRecord:
    id: uuid.UUID
    owner_id: uuid.UUID | None
    run_id: uuid.UUID | None
    job_id: uuid.UUID | None
    purpose: str
    lane: str
    provider: str
    role: str | None
    model: str
    resolved_model: str | None
    prompt_id: str | None
    prompt_version: int | None
    attempt: int
    usage: Usage
    cost_usd: Decimal
    latency_ms: int
    ttft_ms: int | None
    status: str  # ok | error
    error_type: str | None


class LedgerWriter(Protocol):
    async def write(self, rec: LlmCallRecord) -> None: ...


class DbLedgerWriter:
    """One short-lived session per row, opened after the provider call has finished."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]):
        self._sessionmaker = sessionmaker

    async def write(self, rec: LlmCallRecord) -> None:
        async with self._sessionmaker() as s:
            s.add(
                LlmCall(
                    id=rec.id,
                    owner_id=rec.owner_id,
                    run_id=rec.run_id,
                    job_id=rec.job_id,
                    purpose=rec.purpose,
                    lane=rec.lane,
                    provider=rec.provider,
                    role=rec.role,
                    model=rec.model,
                    resolved_model=rec.resolved_model,
                    prompt_id=rec.prompt_id,
                    prompt_version=rec.prompt_version,
                    attempt=rec.attempt,
                    input_tokens=rec.usage.input_tokens,
                    output_tokens=rec.usage.output_tokens,
                    cache_read_tokens=rec.usage.cache_read_tokens,
                    cache_write_tokens=rec.usage.cache_write_tokens,
                    cost_usd=rec.cost_usd,
                    latency_ms=rec.latency_ms,
                    ttft_ms=rec.ttft_ms,
                    status=rec.status,
                    error_type=rec.error_type,
                )
            )
            await s.commit()


class MemoryLedger:
    """In-memory ledger for unit tests and the live smoke test."""

    def __init__(self) -> None:
        self.records: list[LlmCallRecord] = []

    async def write(self, rec: LlmCallRecord) -> None:
        self.records.append(rec)


# --- results ----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CallResult[M: BaseModel]:
    output: M | None  # parsed structured output (schema calls only)
    parts: tuple[TextPart, ...]
    usage: Usage  # of the successful attempt
    cost_usd: Decimal  # summed over all attempts
    stop_reason: str
    model: str
    attempts: int
    call_ids: tuple[uuid.UUID, ...]
    role: str | None = None
    degraded: tuple[str, ...] = ()  # reason codes, e.g. DEGRADED_MODEL_FALLBACK

    @property
    def text(self) -> str:
        return "".join(p.text for p in self.parts)


@dataclass(frozen=True)
class EmbedResult:
    vectors: list[list[float]]
    usage: Usage
    cost_usd: Decimal
    call_ids: tuple[uuid.UUID, ...]


@dataclass(frozen=True)
class _Meta:
    purpose: str
    lane: Lane
    provider: str
    role: str | None
    model: str
    prompt_id: str | None
    prompt_version: int | None
    owner_id: uuid.UUID | None
    run_id: uuid.UUID | None
    job_id: uuid.UUID | None
    est_input_tokens: int

    def span_attrs(self, attempt: int) -> dict[str, str | int]:
        attrs: dict[str, str | int] = {
            "gen_ai.provider.name": self.provider,
            "gen_ai.request.model": self.model,
            "vault.llm.role": self.role or "",
            "vault.llm.purpose": self.purpose,
            "vault.llm.lane": self.lane,
            "vault.llm.attempt": attempt,
        }
        if self.prompt_id is not None and self.prompt_version is not None:
            attrs["vault.llm.prompt_id"] = self.prompt_id
            attrs["vault.llm.prompt_version"] = self.prompt_version
        return attrs


def render_inputs(inputs: Mapping[str, Any]) -> str:
    """Inputs become the user turn as sorted JSON (byte-stable for caching), marked as data."""
    return f"<inputs>{json.dumps(inputs, sort_keys=True, ensure_ascii=False, default=str)}</inputs>"


def _ms(since: float) -> int:
    return int((time.monotonic() - since) * 1000)


def _check_stop(stop_reason: str, refusal_category: str | None) -> None:
    if stop_reason == "refusal":
        raise RefusalError(refusal_category)
    if stop_reason in TRUNCATING_STOPS:
        raise TruncatedError(stop_reason)


@dataclass
class _Account:
    """Running totals across the attempts (and the fallback) of one logical call."""

    ids: list[uuid.UUID]
    total: Decimal = Decimal(0)
    attempts: int = 0


class LlmGateway:
    def __init__(
        self,
        *,
        chat: ChatProvider,
        prompts: PromptRegistry,
        prices: PriceTable,
        ledger: LedgerWriter,
        settings: Settings,
        embeddings: EmbeddingProvider | None = None,
        router: ModelRouter | None = None,
        rate_limiter: RateLimiter | None = None,
        breaker: CircuitBreaker | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rng: Callable[[], float] = random.random,
    ):
        self.chat, self.embeddings, self.prompts, self.prices = chat, embeddings, prompts, prices
        self.ledger, self.settings = ledger, settings
        self.router = router or ModelRouter.from_settings(settings)
        self.rate_limiter = rate_limiter or NoopRateLimiter()
        self.breaker = breaker or NoopCircuitBreaker()
        self._sleep, self._rng = sleep, rng

    # --- public API -----------------------------------------------------------------------------

    @overload
    async def call(
        self,
        *,
        purpose: str,
        lane: Lane,
        prompt: tuple[str, int],
        owner_id: uuid.UUID | None,
        schema: type[T],
        model: str | None = None,
        route: str | None = None,
        mode: str | None = None,
        inputs: Mapping[str, Any] | None = None,
        messages: Sequence[Mapping[str, Any]] | None = None,
        search_results: Sequence[SearchResult] = (),
        run_id: uuid.UUID | None = None,
        job_id: uuid.UUID | None = None,
        budget: Budget | None = None,
    ) -> CallResult[T]: ...

    @overload
    async def call(
        self,
        *,
        purpose: str,
        lane: Lane,
        prompt: tuple[str, int],
        owner_id: uuid.UUID | None,
        schema: None = None,
        model: str | None = None,
        route: str | None = None,
        mode: str | None = None,
        inputs: Mapping[str, Any] | None = None,
        messages: Sequence[Mapping[str, Any]] | None = None,
        search_results: Sequence[SearchResult] = (),
        run_id: uuid.UUID | None = None,
        job_id: uuid.UUID | None = None,
        budget: Budget | None = None,
    ) -> CallResult[BaseModel]: ...

    async def call(
        self,
        *,
        purpose: str,
        lane: Lane,
        prompt: tuple[str, int],
        owner_id: uuid.UUID | None,
        schema: type[BaseModel] | None = None,
        model: str | None = None,
        route: str | None = None,
        mode: str | None = None,
        inputs: Mapping[str, Any] | None = None,
        messages: Sequence[Mapping[str, Any]] | None = None,
        search_results: Sequence[SearchResult] = (),
        run_id: uuid.UUID | None = None,
        job_id: uuid.UUID | None = None,
        budget: Budget | None = None,
    ) -> CallResult[Any]:
        p, req, res = self._prepare(
            prompt, purpose, model, route, mode, inputs, messages, schema, search_results
        )
        meta = self._meta(purpose, lane, self.chat.name, req, p, res.role, owner_id, run_id, job_id)
        parsed: list[BaseModel] = []

        def check(r: ChatResponse) -> None:
            _check_stop(r.stop_reason, r.refusal_category)
            if schema is not None:
                try:
                    parsed.append(schema.model_validate_json(r.text))
                except ValidationError as e:  # message carries counts only, never the model's output
                    raise SchemaValidationError(f"{schema.__name__}: {e.error_count()} errors") from None

        acct = _Account(ids=[])
        degraded: tuple[str, ...] = ()
        try:
            r = await self._run(meta, budget, lambda: self.chat.complete(req), check, acct)
        except (ProviderError, CircuitOpenError) as e:
            expired = (
                budget is not None
                and budget.deadline_at is not None
                and time.monotonic() >= budget.deadline_at
            )
            fb = self.router.fallback(res) if model is None and e.retryable and not expired else None
            if fb is None:
                raise
            # One attempt on the next role up (no retries); a second failure propagates.
            req = replace(req, model=fb.model, effort=fb.effort)
            meta = replace(meta, role=fb.role, model=fb.model)
            parsed.clear()
            r = await self._run(meta, budget, lambda: self.chat.complete(req), check, acct, retries=0)
            degraded = (DEGRADED_MODEL_FALLBACK,)
        return CallResult(
            output=parsed[0] if parsed else None,
            parts=r.parts,
            usage=r.usage,
            cost_usd=acct.total,
            stop_reason=r.stop_reason,
            model=req.model,
            attempts=acct.attempts,
            call_ids=tuple(acct.ids),
            role=meta.role,
            degraded=degraded,
        )

    async def stream(
        self,
        *,
        purpose: str,
        lane: Lane,
        prompt: tuple[str, int],
        owner_id: uuid.UUID | None,
        schema: type[BaseModel] | None = None,
        model: str | None = None,
        route: str | None = None,
        mode: str | None = None,
        inputs: Mapping[str, Any] | None = None,
        messages: Sequence[Mapping[str, Any]] | None = None,
        search_results: Sequence[SearchResult] = (),
        run_id: uuid.UUID | None = None,
        job_id: uuid.UUID | None = None,
        budget: Budget | None = None,
    ) -> AsyncIterator[TextDelta | CitationDelta | Final]:
        """Yields TextDelta | CitationDelta, then Final. With `schema`, the deltas are fragments of the strict
        JSON document and the consumer parses them incrementally. Retries only before the first event was
        yielded; refusal / max_tokens raise typed errors after the row is written."""
        p, req, res = self._prepare(
            prompt, purpose, model, route, mode, inputs, messages, schema, search_results
        )
        meta = self._meta(purpose, lane, self.chat.name, req, p, res.role, owner_id, run_id, job_id)
        self._admit(meta, budget)
        ids: list[uuid.UUID] = []
        total = Decimal(0)
        attempt = 0
        while True:
            attempt += 1
            await self._acquire(meta)
            t0 = time.monotonic()
            ttft: int | None = None
            final: ProviderFinal | None = None
            recorded = False
            span = _tracer.start_span("llm.stream", attributes=meta.span_attrs(attempt))
            try:
                async with aclosing(self.chat.stream(req)) as events:
                    async for ev in events:
                        if isinstance(ev, ProviderFinal):
                            final = ev
                            continue
                        if ttft is None:
                            ttft = _ms(t0)
                        yield ev
                if final is None:
                    raise ProviderConnectionError("stream ended without a final event")
            except ProviderError as e:
                ids.append(
                    await self._record(meta, attempt, Usage(), Decimal(0), _ms(t0), ttft, e.error_type, None)
                )
                recorded = True
                self._on_failure(meta, e, span)
                delay = None if ttft is not None else self._retry_delay(e, attempt, budget)
                if delay is None:
                    raise
            except BaseException as e:  # consumer closed / cancelled / unexpected: still one row
                if not recorded:
                    kind = (
                        "cancelled" if isinstance(e, GeneratorExit | asyncio.CancelledError) else "unexpected"
                    )
                    await self._record(meta, attempt, Usage(), Decimal(0), _ms(t0), ttft, kind, None)
                raise
            else:
                cost = self.prices.cost(meta.model, final.usage)
                total += cost
                self._on_success(meta, final.usage, budget, span)
                err: LlmError | None = None
                try:
                    _check_stop(final.stop_reason, final.refusal_category)
                except LlmError as e:
                    err = e
                ids.append(
                    await self._record(
                        meta,
                        attempt,
                        final.usage,
                        cost,
                        _ms(t0),
                        ttft,
                        err.error_type if err else None,
                        final.resolved_model,
                    )
                )
                if err is not None:
                    span.set_attribute("error.type", err.error_type)
                    raise err
                yield Final(
                    usage=final.usage, stop_reason=final.stop_reason, cost_usd=total, call_ids=tuple(ids)
                )
                return
            finally:
                span.end()
            await self._sleep(delay)

    async def embed(
        self,
        *,
        purpose: str,
        lane: Lane,
        texts: Sequence[str],
        owner_id: uuid.UUID | None,
        model: str | None = None,
        run_id: uuid.UUID | None = None,
        job_id: uuid.UUID | None = None,
        budget: Budget | None = None,
    ) -> EmbedResult:
        if self.embeddings is None:
            raise RequestConflictError("no embedding provider configured")
        if not texts or len(texts) > MAX_EMBED_BATCH:
            raise RequestConflictError(f"embed batch must hold 1..{MAX_EMBED_BATCH} texts")
        embeddings, dims = self.embeddings, self.settings.embedding_dim
        model_ = model or self.settings.embedding_model
        meta = _Meta(
            purpose,
            lane,
            embeddings.name,
            None,
            model_,
            None,
            None,
            owner_id,
            run_id,
            job_id,
            sum(len(t) for t in texts) // 4,
        )

        def check(r: EmbeddingResponse) -> None:
            if len(r.vectors) != len(texts) or any(len(v) != dims for v in r.vectors):
                raise SchemaValidationError("embedding count or dimension mismatch")

        acct = _Account(ids=[])
        r = await self._run(meta, budget, lambda: embeddings.embed(model_, texts, dims), check, acct)
        return EmbedResult(vectors=r.vectors, usage=r.usage, cost_usd=acct.total, call_ids=tuple(acct.ids))

    # --- internals ------------------------------------------------------------------------------

    def _prepare(
        self,
        prompt: tuple[str, int],
        node: str,
        model: str | None,
        route: str | None,
        mode: str | None,
        inputs: Mapping[str, Any] | None,
        messages: Sequence[Mapping[str, Any]] | None,
        schema: type[BaseModel] | None,
        search_results: Sequence[SearchResult],
    ) -> tuple[Prompt, ChatRequest, Resolved]:
        p = self.prompts.get(*prompt)
        if schema is not None and search_results:
            raise RequestConflictError("citations and output_config.format cannot be combined in one request")
        if (p.output == "schema") != (schema is not None):
            raise RequestConflictError(f"{p.id}@{p.version} has output={p.output}; schema mismatch")
        if search_results and p.output != "citations":
            raise RequestConflictError(
                f"{p.id}@{p.version} has output={p.output}; search results not allowed"
            )
        if (inputs is None) == (messages is None):
            raise RequestConflictError("pass exactly one of inputs / messages")
        msgs = (
            [dict(m) for m in messages]
            if messages is not None
            else [{"role": "user", "content": render_inputs(inputs or {})}]
        )
        # Table row for the node (most specific wins), else the prompt's front-matter role and effort.
        res = self.router.resolve(node, route, mode, default=Route(p.role, p.effort))
        req = ChatRequest(
            model=model or res.model,  # an explicit `model` pins the call and bypasses role fallback
            system=p.body,
            messages=msgs,
            max_tokens=p.max_tokens,
            effort=res.effort,
            output_schema=schema.model_json_schema() if schema is not None else None,
            search_results=tuple(search_results),
        )
        return p, req, res

    @staticmethod
    def _meta(
        purpose: str,
        lane: Lane,
        provider: str,
        req: ChatRequest,
        p: Prompt,
        role: str,
        owner_id: uuid.UUID | None,
        run_id: uuid.UUID | None,
        job_id: uuid.UUID | None,
    ) -> _Meta:
        chars = len(req.system) + sum(len(str(m.get("content", ""))) for m in req.messages)
        chars += sum(len(s) for sr in req.search_results for s in sr.sentences)
        return _Meta(
            purpose, lane, provider, role, req.model, p.id, p.version, owner_id, run_id, job_id, chars // 4
        )

    def _admit(self, meta: _Meta, budget: Budget | None) -> None:
        self.prices.price(meta.model)  # unknown price → fail before spending anything
        if budget is not None:
            budget.charge_call()

    async def _acquire(self, meta: _Meta) -> None:
        if not self.breaker.allow(meta.model):
            raise CircuitOpenError(meta.model)
        await self.rate_limiter.acquire(
            model=meta.model, lane=meta.lane, est_input_tokens=meta.est_input_tokens
        )

    def _on_failure(self, meta: _Meta, e: ProviderError, span: trace.Span) -> None:
        span.set_attribute("error.type", e.error_type)
        span.set_status(trace.StatusCode.ERROR)
        self.breaker.record_failure(meta.model, e.error_type)
        if isinstance(e, RateLimitedError):
            self.rate_limiter.on_rate_limited(model=meta.model, retry_after=e.retry_after)

    def _on_success(self, meta: _Meta, usage: Usage, budget: Budget | None, span: trace.Span) -> None:
        span.set_attribute("gen_ai.usage.input_tokens", usage.input_tokens)
        span.set_attribute("gen_ai.usage.output_tokens", usage.output_tokens)
        self.breaker.record_success(meta.model)
        self.rate_limiter.reconcile(model=meta.model, usage=usage)
        if budget is not None:
            budget.record_usage(usage)

    def _retry_delay(
        self, e: ProviderError, attempt: int, budget: Budget | None, retries: int | None = None
    ) -> float | None:
        """None = do not retry. Honors retry-after (plus jitter); otherwise jittered exponential backoff."""
        s = self.settings
        cap_retries = s.llm_max_retries if retries is None else retries
        if not e.retryable or attempt > cap_retries:
            return None
        if e.retry_after is not None:
            delay = e.retry_after + self._rng() * s.llm_retry_base_s
        else:
            cap = min(s.llm_retry_max_s, s.llm_retry_base_s * 2 ** (attempt - 1))
            delay = cap / 2 + self._rng() * cap / 2
        if (
            budget is not None
            and budget.deadline_at is not None
            and time.monotonic() + delay > budget.deadline_at
        ):
            return None
        return delay

    async def _record(
        self,
        meta: _Meta,
        attempt: int,
        usage: Usage,
        cost: Decimal,
        latency_ms: int,
        ttft_ms: int | None,
        error_type: str | None,
        resolved_model: str | None,
    ) -> uuid.UUID:
        rec = LlmCallRecord(
            id=uuid7(),
            owner_id=meta.owner_id,
            run_id=meta.run_id,
            job_id=meta.job_id,
            purpose=meta.purpose,
            lane=meta.lane,
            provider=meta.provider,
            role=meta.role,
            model=meta.model,
            resolved_model=resolved_model,
            prompt_id=meta.prompt_id,
            prompt_version=meta.prompt_version,
            attempt=attempt,
            usage=usage,
            cost_usd=cost,
            latency_ms=latency_ms,
            ttft_ms=ttft_ms,
            status="ok" if error_type is None else "error",
            error_type=error_type,
        )
        await self.ledger.write(rec)
        return rec.id

    async def _run(
        self,
        meta: _Meta,
        budget: Budget | None,
        op: Callable[[], Awaitable[R]],
        check: Callable[[R], None],
        acct: _Account,
        retries: int | None = None,
    ) -> R:
        """Attempt loop for request/response calls. `check` raises an LlmError for unusable results
        (refusal, truncation, schema), which is recorded on the attempt's row and never retried.
        `retries` overrides the configured retry cap (0 for the single role-fallback attempt)."""
        self._admit(meta, budget)
        attempt = 0
        while True:
            attempt += 1
            acct.attempts += 1
            await self._acquire(meta)
            t0 = time.monotonic()
            with _tracer.start_as_current_span("llm.call", attributes=meta.span_attrs(acct.attempts)) as span:
                try:
                    r = await op()
                except ProviderError as e:
                    acct.ids.append(
                        await self._record(
                            meta, attempt, Usage(), Decimal(0), _ms(t0), None, e.error_type, None
                        )
                    )
                    self._on_failure(meta, e, span)
                    delay = self._retry_delay(e, attempt, budget, retries)
                    if delay is None:
                        raise
                except (Exception, asyncio.CancelledError) as e:
                    kind = "cancelled" if isinstance(e, asyncio.CancelledError) else "unexpected"
                    await self._record(meta, acct.attempts, Usage(), Decimal(0), _ms(t0), None, kind, None)
                    raise
                else:
                    usage = _usage_of(r)
                    cost = self.prices.cost(meta.model, usage)
                    acct.total += cost
                    self._on_success(meta, usage, budget, span)
                    err: LlmError | None = None
                    try:
                        check(r)
                    except LlmError as e:
                        err = e
                    acct.ids.append(
                        await self._record(
                            meta,
                            acct.attempts,
                            usage,
                            cost,
                            _ms(t0),
                            None,
                            err.error_type if err else None,
                            getattr(r, "resolved_model", None),
                        )
                    )
                    if err is not None:
                        span.set_attribute("error.type", err.error_type)
                        raise err
                    return r
            await self._sleep(delay)


def _usage_of(r: object) -> Usage:
    usage = getattr(r, "usage", None)
    if not isinstance(usage, Usage):
        raise TypeError("provider result has no Usage")
    return usage


@lru_cache(maxsize=1)
def get_gateway() -> LlmGateway:
    from app.db.session import get_sessionmaker
    from app.llm.providers.openai import OpenAIEmbeddingProvider
    from app.llm.providers.openai_chat import OpenAIResponsesProvider

    s = get_settings()
    openai_key = s.openai_api_key.get_secret_value() if s.openai_api_key else None
    return LlmGateway(
        chat=OpenAIResponsesProvider(openai_key, s.llm_timeout_s),
        embeddings=OpenAIEmbeddingProvider(openai_key, s.llm_timeout_s),
        prompts=default_registry(),
        prices=price_table_from_settings(s),
        ledger=DbLedgerWriter(get_sessionmaker()),
        settings=s,
    )
