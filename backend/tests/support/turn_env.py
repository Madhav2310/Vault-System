"""A real turn over the test database with the fake provider: seed one chunk, script the model, install the
runner. Shared by the streaming, run-explanation and follow-up integration tests."""

import json
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import get_turn_runner
from app.chat.turn import TurnDeps, run_turn
from app.core.ids import uuid7
from app.ingestion.sentences import split_sentences
from app.llm.gateway import DbLedgerWriter
from app.llm.providers.fake import FakeProvider, Step, structured
from app.retrieval.config import RetrievalConfig
from app.retrieval.query_embed import QueryEmbedder
from app.retrieval.rerank import RerankClient
from app.retrieval.retrieve import Retriever
from tests.support.chat_gen import fake_gateway
from tests.support.docs import seed_document

BODY = (
    "The Consultant shall provide the Services for twelve months. "
    "Either party may terminate on 30 days notice."
)
EMBED_SLOT = structured({})  # the fake provider's embed() pops one scripted step


def analysis(query: str, **over: Any) -> Any:
    return structured(
        {
            "standalone_query": query,
            "target_refs": [],
            "dimensions": [],
            "intent": "qa",
            "needs_whole_document": False,
            "clarification": None,
            **over,
        }
    )


def answer(*segments: tuple[str, list[str]], **extra: Any) -> Any:
    return structured(
        {"status": "answer", "segments": [{"text": t, "evidence": e} for t, e in segments], **extra}
    )


def rerank_client() -> RerankClient:
    def handler(request: httpx.Request) -> httpx.Response:
        n = len(json.loads(request.content)["passages"])
        return httpx.Response(200, json={"scores": [1.0 - i / 100 for i in range(n)], "model": "m"})

    return RerankClient("http://reranker.test", 800, transport=httpx.MockTransport(handler))


async def seed_chunk(
    sm: async_sessionmaker[AsyncSession], owner_id: uuid.UUID, title: str = "Doc"
) -> uuid.UUID:
    doc = await seed_document(sm, owner_id, title=title)
    cid = uuid7()
    sents = [list(se) for se in split_sentences(BODY)]
    async with sm() as s:
        await s.execute(
            text(
                "INSERT INTO chunks (id, owner_id, document_id, version_id, ordinal, kind, original_text, "
                "embedding_text, page_start, page_end, section_path, sentences, spans, token_count, "
                "content_hash) VALUES (:id, :o, :d, :v, 0, 'text', :t, :t, 3, 3, '{Term}', "
                "CAST(:sent AS jsonb), CAST('[]' AS jsonb), 20, 'h')"
            ),
            {"id": cid, "o": owner_id, "d": doc.document_id, "v": doc.version_id, "t": BODY,
             "sent": json.dumps(sents)},
        )  # fmt: skip
        await s.commit()
    return cid


def install_runner(
    api: httpx.AsyncClient,
    sm: async_sessionmaker[AsyncSession],
    script: list[Step],
    before: Callable[[], Awaitable[None]] | None = None,
    fast_path: bool = False,
    cfg: RetrievalConfig | None = None,
) -> FakeProvider:
    """Override the turn runner with a real `run_turn` over a scripted fake provider (`before` runs first)."""
    gw, prov = fake_gateway(script, ledger=DbLedgerWriter(sm), fast_path=fast_path)
    deps = TurnDeps(sm, gw, Retriever(QueryEmbedder(gw, None), rerank_client(), None, cfg))

    async def runner(*a: Any) -> None:
        if before is not None:
            await before()
        await run_turn(deps, *a)

    api._transport.app.dependency_overrides[get_turn_runner] = lambda: runner  # type: ignore[attr-defined]
    return prov


def answer_script(query: str, *segments: tuple[str, list[str]], **extra: Any) -> list[Step]:
    steps: list[Step] = [analysis(query), EMBED_SLOT, answer(*segments, **extra)]
    cited = [i for i, (_, ev) in enumerate(segments) if ev]
    if cited and not extra:  # the support check (S17.09) is a third model call when something is cited
        steps.append(
            structured({"verdicts": [{"index": i, "verdict": "supported", "reason": "ok"} for i in cited]})
        )
    return steps


async def new_session(api: httpx.AsyncClient, owner: Any) -> uuid.UUID:
    return uuid.UUID((await api.post("/v1/chat/sessions", json={}, headers=owner.auth)).json()["id"])


def post(
    api: httpx.AsyncClient, owner: Any, sid: uuid.UUID, content: str, cmid: str, sse: bool = False
) -> Awaitable[httpx.Response]:
    headers = {**owner.auth, **({"Accept": "text/event-stream"} if sse else {})}
    return api.post(
        f"/v1/chat/sessions/{sid}/messages",
        json={"content": content, "client_message_id": cmid},
        headers=headers,
    )


def parse_sse(body: str) -> list[dict[str, Any]]:
    """The events of an SSE body (comments skipped), as parsed JSON dicts."""
    out: list[dict[str, Any]] = []
    for frame in body.split("\n\n"):
        data = [ln[5:].strip() for ln in frame.splitlines() if ln.startswith("data:")]
        if data:
            out.append(json.loads("".join(data)))
    return out
