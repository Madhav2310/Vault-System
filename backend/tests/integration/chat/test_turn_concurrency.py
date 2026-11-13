"""S17.14 intra-turn concurrency over the real database: analyze overlaps the question embedding, vector and
lexical search run on separate sessions with identical results, and no pooled connection is checked out while
an LLM (or reranker) call is awaited."""

import asyncio
import json
import random
import time
import uuid
from collections.abc import AsyncGenerator, Callable, Sequence
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

import app.retrieval.retrieve as retrieve_mod
from app.chat import sessions as svc
from app.chat.turn import TurnDeps, run_turn
from app.core.ids import uuid7
from app.core.settings import Settings
from app.ingestion.sentences import split_sentences
from app.llm.gateway import DbLedgerWriter, LlmGateway
from app.llm.pricing import DEFAULT_PRICES, PriceTable
from app.llm.prompts import default_registry
from app.llm.providers.fake import FakeProvider, Step, structured
from app.llm.types import ChatRequest, ChatResponse, EmbeddingResponse, StreamEvent
from app.retrieval.lexical import lexical_search
from app.retrieval.query_embed import QueryEmbedder
from app.retrieval.rerank import RerankClient
from app.retrieval.retrieve import RetrievalResult, Retriever
from app.retrieval.scope import Scope, compute_scope
from app.retrieval.vector import vector_search
from tests.support.docs import seed_document
from tests.support.ledger_env import ledger_owners
from tests.support.llm import SleepRecorder, make_gateway
from tests.support.retrieval import seed_text_chunk
from tests.support.vectors import seed_vectors, unit_vector

__all__ = ["ledger_owners"]  # fixture import

SM = async_sessionmaker[AsyncSession]
BODY = (
    "The Consultant shall provide the Services for twelve months. "
    "Either party may terminate on 30 days notice."
)
EMBED_SLOT = structured({})  # the fake provider's embed() pops one scripted step


def _analysis(query: str) -> Any:
    return structured(
        {
            "standalone_query": query,
            "target_refs": [],
            "dimensions": [],
            "intent": "qa",
            "needs_whole_document": False,
            "clarification": None,
        }
    )


def _out(engine: AsyncEngine) -> int:
    return int(getattr(engine.pool, "checkedout")())  # noqa: B009 (AsyncEngine.pool is typed as Pool)


class ProbeProvider(FakeProvider):
    """FakeProvider with per-call latencies applied after the scripted step is popped (so concurrent calls
    keep the scripted order) and a probe of the pool's checked-out connections at each latency window."""

    def __init__(
        self, script: Sequence[Step], engine: AsyncEngine, analyze_s: float, embed_s: float, gen_s: float
    ) -> None:
        super().__init__(script=list(script))
        self.engine, self.analyze_s, self.embed_s, self.gen_s = engine, analyze_s, embed_s, gen_s
        self.checked_out: list[tuple[str, int]] = []

    def _probe(self, kind: str) -> None:
        self.checked_out.append((kind, _out(self.engine)))

    async def complete(self, req: ChatRequest) -> ChatResponse:
        res = await super().complete(req)
        self._probe("analyze")
        await asyncio.sleep(self.analyze_s)
        return res

    async def stream(self, req: ChatRequest) -> AsyncGenerator[StreamEvent, None]:
        first = True
        async for ev in super().stream(req):
            if first:
                first = False
                self._probe("generate")
                await asyncio.sleep(self.gen_s)
            yield ev

    async def embed(self, model: str, texts: Sequence[str], dimensions: int) -> EmbeddingResponse:
        res = await super().embed(model, texts, dimensions)
        self._probe("embed")
        await asyncio.sleep(self.embed_s)
        return res


def _gateway(prov: FakeProvider, sm: SM) -> LlmGateway:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None, database_url="postgresql+asyncpg://x/y", redis_url="redis://x", chat_fast_path=False
    )
    return LlmGateway(
        chat=prov,
        embeddings=prov,
        prompts=default_registry(),
        prices=PriceTable({**DEFAULT_PRICES}),
        ledger=DbLedgerWriter(sm),
        settings=settings,
        sleep=SleepRecorder(),
        rng=lambda: 0.5,
    )


def _rerank(on_call: Callable[[], None] | None = None) -> RerankClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if on_call:
            on_call()
        n = len(json.loads(request.content)["passages"])
        return httpx.Response(200, json={"scores": [1.0 - i / 100 for i in range(n)], "model": "m"})

    return RerankClient("http://reranker.test", 800, transport=httpx.MockTransport(handler))


class TimedRetriever(Retriever):
    started_at: float | None = None

    async def retrieve(self, *a: Any, **kw: Any) -> RetrievalResult:
        self.started_at = time.monotonic()
        return await super().retrieve(*a, **kw)


async def _seed_chunk(sm: SM, owner_id: uuid.UUID) -> None:
    doc = await seed_document(sm, owner_id, title="Services Agreement")
    sents = [list(se) for se in split_sentences(BODY)]
    async with sm() as s:
        await s.execute(
            text(
                "INSERT INTO chunks (id, owner_id, document_id, version_id, ordinal, kind, original_text, "
                "embedding_text, page_start, page_end, section_path, sentences, spans, token_count, "
                "content_hash) VALUES (:id, :o, :d, :v, 0, 'text', :t, :t, 3, 3, '{Term}', "
                "CAST(:sent AS jsonb), CAST('[]' AS jsonb), 20, 'h')"
            ),
            {"id": uuid7(), "o": owner_id, "d": doc.document_id, "v": doc.version_id, "t": BODY,
             "sent": json.dumps(sents)},
        )  # fmt: skip
        await s.commit()


async def _start(sm: SM, owner_id: uuid.UUID, q: str) -> Any:
    async with sm() as s:
        sid = (
            await s.execute(
                text("INSERT INTO chat_sessions (id, owner_id) VALUES (gen_random_uuid(), :o) RETURNING id"),
                {"o": owner_id},
            )
        ).scalar_one()
        await s.commit()
    async with sm() as s:
        return await svc.start_turn(s, owner_id, sid, q, "m1"), sid


async def _run(sm: SM, engine: AsyncEngine, ledger_owners: Callable[[], Any], **lat: float) -> Any:
    o = await ledger_owners()
    await _seed_chunk(sm, o.id)
    q = "Services twelve months"
    script = [
        _analysis(q),
        EMBED_SLOT,
        structured({"status": "answer", "segments": [{"text": "Twelve months.", "evidence": ["E1.0"]}]}),
    ]
    prov = ProbeProvider(script, engine, lat.get("analyze_s", 0), lat.get("embed_s", 0), lat.get("gen_s", 0))
    gw = _gateway(prov, sm)
    pooled: list[int] = []
    retriever = TimedRetriever(
        QueryEmbedder(gw, None),
        _rerank(lambda: pooled.append(_out(engine))),
        None,
    )
    deps = TurnDeps(sm, gw, retriever)
    t, sid = await _start(sm, o.id, q)
    t0 = time.monotonic()
    await run_turn(deps, o.id, sid, t.run_id, t.user_message_id, q)
    return t0, retriever, prov, pooled, t.run_id, o.id


async def test_analyze_and_embed_overlap(
    sm: SM, engine: AsyncEngine, ledger_owners: Callable[[], Any]
) -> None:
    t0, retriever, prov, _, run_id, owner_id = await _run(
        sm, engine, ledger_owners, analyze_s=1.0, embed_s=0.5
    )
    assert retriever.started_at is not None
    assert retriever.started_at - t0 <= 1.0 + 0.2  # sequential would be 1.5 + db time
    assert len(prov.embed_calls) == 1  # the speculative embedding was reused by retrieval
    async with sm() as s:
        row = (
            await s.execute(
                text(
                    "SELECT status, response_type FROM rag_runs r JOIN chat_messages m ON m.run_id = r.id "
                    "WHERE r.id = :r AND m.role = 'assistant'"
                ),
                {"r": run_id},
            )
        ).one()
        nodes = (
            await s.execute(
                text("SELECT node, decision FROM run_steps WHERE run_id = :r ORDER BY seq"), {"r": run_id}
            )
        ).all()
    assert row == ("completed", "answer")
    by_node: dict[str, Any] = {n: d for n, d in nodes}
    names = [n for n, _ in nodes]
    assert names[:3] == ["load_context", "route_question", "analyze"] and names[-1] != "persist"
    retrieve_decision = by_node["retrieve"]
    for key in ("embed_ms", "vector_ms", "lexical_ms", "rerank_ms", "expand_ms"):
        assert key in retrieve_decision, key
    assert retrieve_decision["embed_reused"] is True
    _ = owner_id


async def test_no_connection_across_llm(
    sm: SM, engine: AsyncEngine, ledger_owners: Callable[[], Any]
) -> None:
    _, _, prov, pooled, _, _ = await _run(sm, engine, ledger_owners, analyze_s=0.1, embed_s=0.1, gen_s=0.1)
    kinds = {k for k, _ in prov.checked_out}
    assert {"analyze", "embed", "generate"} <= kinds
    assert all(n == 0 for _, n in prov.checked_out), prov.checked_out
    assert pooled and all(n == 0 for n in pooled)  # nor across the reranker call


async def test_vector_lexical_concurrent_same_results(
    tmp_path: Any, sm: SM, make_owner: Callable[[], Any], monkeypatch: Any
) -> None:
    o = await make_owner()
    doc = await seed_document(sm, o.id)
    rng = random.Random(7)
    await seed_vectors(sm, o.id, doc, [unit_vector(rng) for _ in range(14)])
    await seed_text_chunk(sm, o.id, doc, "The indemnification clause survives termination.", ordinal=50)
    gw, _prov, _, _ = make_gateway(tmp_path)
    r = Retriever(QueryEmbedder(gw, None), _rerank(), None)

    spans: list[tuple[str, int, float, float]] = []
    real_vec, real_lex = vector_search, lexical_search

    def timed(name: str, fn: Any) -> Any:
        async def wrapper(session: AsyncSession, *a: Any, **kw: Any) -> Any:
            t = time.monotonic()
            await asyncio.sleep(0.15)  # make an overlap measurable
            out = await fn(session, *a, **kw)
            spans.append((name, id(session), t, time.monotonic()))
            return out

        return wrapper

    monkeypatch.setattr(retrieve_mod, "vector_search", timed("vec", real_vec))
    monkeypatch.setattr(retrieve_mod, "lexical_search", timed("lex", real_lex))
    q = "indemnification clause"
    async with sm() as s:
        scope: Scope = await compute_scope(s, o.id, None)
        sequential = await r.retrieve(s, o.id, scope, q)  # one session: sequential
    seq_spans, spans[:] = list(spans), []
    concurrent = await r.retrieve(sm, o.id, scope, q)  # sessionmaker: concurrent, separate sessions

    assert len({sid for _, sid, _, _ in seq_spans}) == 1
    (_, _, _a0, a1), (_, _, b0, _b1) = sorted(seq_spans, key=lambda x: x[2])
    assert b0 >= a1 - 0.01  # sequential: no overlap
    assert len({sid for _, sid, _, _ in spans}) == 2  # two sessions
    (_, _, _c0, c1), (_, _, d0, _d1) = sorted(spans, key=lambda x: x[2])
    assert d0 < c1  # the second search started before the first one finished
    assert sequential.selected and concurrent.selected == sequential.selected
    assert concurrent.candidates == sequential.candidates
    assert set(concurrent.timings) >= {"embed_ms", "vector_ms", "lexical_ms", "rerank_ms", "expand_ms"}
