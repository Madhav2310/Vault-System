"""Rerank service: fastembed cross-encoder with bounded in-flight work and 503 backpressure.

POST /rerank {query, passages[]} -> {scores[], model}. Design §4.3, §7.3, §13.1.
"""

import asyncio
import os
from collections.abc import AsyncIterator, Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from opentelemetry import trace
from pydantic import BaseModel, Field

from models import DEFAULT_MODEL, ModelSpec, resolve_model

Scorer = Callable[[str, list[str]], list[float]]
tracer = trace.get_tracer("vault.reranker")


@dataclass(frozen=True)
class Config:
    model: str = field(default_factory=lambda: os.environ.get("RERANKER_MODEL", DEFAULT_MODEL))
    max_in_flight: int = field(
        default_factory=lambda: int(os.environ.get("RERANKER_MAX_IN_FLIGHT", os.cpu_count() or 1))
    )
    queue_timeout_ms: int = field(
        default_factory=lambda: int(os.environ.get("RERANKER_QUEUE_TIMEOUT_MS", "800"))
    )
    max_passages: int = field(
        default_factory=lambda: int(os.environ.get("RERANKER_MAX_PASSAGES", "100"))
    )
    max_passage_chars: int = field(
        default_factory=lambda: int(os.environ.get("RERANKER_MAX_PASSAGE_CHARS", "8000"))
    )
    max_query_chars: int = field(
        default_factory=lambda: int(os.environ.get("RERANKER_MAX_QUERY_CHARS", "2000"))
    )
    cache_dir: str | None = field(default_factory=lambda: os.environ.get("FASTEMBED_CACHE_PATH"))


class RerankResponse(BaseModel):
    scores: list[float]
    model: str


def load_scorer(spec: ModelSpec, cfg: Config) -> Scorer:
    from fastembed.rerank.cross_encoder import TextCrossEncoder

    # One shared session; ONNX Runtime run() and the Rust tokenizer are thread-safe.
    encoder = TextCrossEncoder(
        model_name=spec.hf_id, cache_dir=cfg.cache_dir, threads=cfg.max_in_flight
    )

    def score(query: str, passages: list[str]) -> list[float]:
        return [float(s) for s in encoder.rerank(query, passages)]

    return score


def create_app(config: Config | None = None, scorer: Scorer | None = None) -> FastAPI:
    """Build the app. Raises ModelNotAllowedError immediately for a non-allowlisted model.

    `scorer` is an injection point for tests; when None the fastembed model loads at startup.
    """
    cfg: Config = config or Config()
    spec = resolve_model(cfg.model)

    class RerankRequest(BaseModel):
        query: str = Field(min_length=1, max_length=cfg.max_query_chars)
        passages: list[str] = Field(max_length=cfg.max_passages)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.scorer = scorer or load_scorer(spec, cfg)
        app.state.executor = ThreadPoolExecutor(cfg.max_in_flight, thread_name_prefix="rerank")
        app.state.sem = asyncio.Semaphore(cfg.max_in_flight)
        yield
        app.state.executor.shutdown(wait=False, cancel_futures=True)

    app = FastAPI(title="vault-reranker", version="0.2.0", lifespan=lifespan)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "model": spec.hf_id}

    @app.post("/rerank", response_model=None)
    async def rerank(req: RerankRequest, request: Request) -> RerankResponse | JSONResponse:
        if any(len(p) > cfg.max_passage_chars for p in req.passages):
            return JSONResponse(
                status_code=422, content={"code": "passage_too_long", "retryable": False}
            )
        with tracer.start_as_current_span("rerank") as span:
            span.set_attribute("rerank.model", spec.hf_id)
            span.set_attribute("rerank.passages", len(req.passages))
            if not req.passages:
                return RerankResponse(scores=[], model=spec.hf_id)
            sem: asyncio.Semaphore = request.app.state.sem
            try:
                await asyncio.wait_for(sem.acquire(), cfg.queue_timeout_ms / 1000)
            except TimeoutError:
                span.set_attribute("rerank.outcome", "overloaded")
                return JSONResponse(
                    status_code=503, content={"code": "overloaded", "retryable": True}
                )
            loop = asyncio.get_running_loop()
            # Release the slot when the thread actually finishes (even if the client disconnects),
            # so in-flight inference never exceeds max_in_flight.
            try:
                cf = request.app.state.executor.submit(
                    request.app.state.scorer, req.query, req.passages
                )
            except BaseException:
                sem.release()
                raise
            cf.add_done_callback(lambda _f: loop.call_soon_threadsafe(sem.release))
            scores = await asyncio.wrap_future(cf)
            span.set_attribute("rerank.outcome", "ok")
            return RerankResponse(scores=scores, model=spec.hf_id)

    return app


def _init_tracing() -> None:
    if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    name = os.environ.get("OTEL_SERVICE_NAME", "vault-reranker")
    provider = TracerProvider(resource=Resource.create({"service.name": name}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)


_init_tracing()
app = create_app()
