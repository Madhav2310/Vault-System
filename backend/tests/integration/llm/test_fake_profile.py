"""LLM_PROVIDER=fake: scripted questions get cited answers through the API with no provider key (S19.02)."""

import json
import pathlib
import uuid
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import get_turn_runner
from app.chat.turn import TurnDeps, run_turn
from app.core.ids import uuid7
from app.core.settings import Settings
from app.ingestion.sentences import split_sentences
from app.llm.gateway import build_gateway
from app.retrieval.query_embed import QueryEmbedder
from app.retrieval.rerank import RerankClient
from app.retrieval.retrieve import Retriever
from tests.integration.chat.conftest import owners
from tests.support.docs import seed_document
from tests.support.ledger_env import ledger_owners

__all__ = ["ledger_owners", "owners"]  # fixture import

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "fixtures" / "ui_script.yaml"
BODY = (
    "The Consultant shall provide the Services for twelve months. "
    "Either party may terminate this Agreement on 30 days written notice."
)


def _rerank() -> RerankClient:
    def handler(request: httpx.Request) -> httpx.Response:
        n = len(json.loads(request.content)["passages"])
        return httpx.Response(200, json={"scores": [1.0 - i / 100 for i in range(n)], "model": "m"})

    return RerankClient("http://reranker.test", 800, transport=httpx.MockTransport(handler))


async def _seed_chunk(sm: async_sessionmaker[AsyncSession], owner_id: uuid.UUID) -> uuid.UUID:
    doc = await seed_document(sm, owner_id, title="Services Agreement")
    cid = uuid7()
    sents = [list(se) for se in split_sentences(BODY)]
    async with sm() as s:
        await s.execute(
            text(
                "INSERT INTO chunks (id, owner_id, document_id, version_id, ordinal, kind, original_text, "
                "embedding_text, page_start, page_end, section_path, sentences, spans, token_count, "
                "content_hash) VALUES (:id, :o, :d, :v, 0, 'text', :t, :t, 1, 1, '{Term}', "
                "CAST(:sent AS jsonb), CAST('[]' AS jsonb), 20, 'h')"
            ),
            {"id": cid, "o": owner_id, "d": doc.document_id, "v": doc.version_id, "t": BODY,
             "sent": json.dumps(sents)},
        )  # fmt: skip
        await s.commit()
    return cid


async def test_answers_without_key(
    sm: async_sessionmaker[AsyncSession],
    ledger_owners: Callable[[], Any],
    api: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UI_SCRIPT_PATH", str(SCRIPT))
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        database_url="postgresql+asyncpg://x/y",
        redis_url="redis://x",
        llm_provider="fake",
        openai_api_key=None,
    )
    o = await ledger_owners()
    await _seed_chunk(sm, o.id)
    gw = build_gateway(sm, settings)
    deps = TurnDeps(sm, gw, Retriever(QueryEmbedder(gw, None), _rerank(), None))

    async def runner(*a: Any) -> None:
        await run_turn(deps, *a)

    api._transport.app.dependency_overrides[get_turn_runner] = lambda: runner  # type: ignore[attr-defined]
    sid = uuid.UUID((await api.post("/v1/chat/sessions", json={}, headers=o.auth)).json()["id"])

    async def ask(q: str, cmid: str) -> dict[str, Any]:
        r = await api.post(
            f"/v1/chat/sessions/{sid}/messages",
            json={"content": q, "client_message_id": cmid},
            headers=o.auth,
        )
        assert r.status_code == 200, r.text
        msg: dict[str, Any] = r.json()["message"]
        return msg

    msg = await ask("What is the notice period for termination?", "m1")
    assert msg["response_type"] == "answer"
    h = (await api.get(f"/v1/chat/sessions/{sid}/messages", headers=o.auth)).json()["items"]
    cited = [e["cited_text"] for seg in h[1]["segments"] for e in seg["evidence"]]
    assert any("30 days written notice" in c for c in cited) and any("twelve months" in c for c in cited)

    assert (await ask("What is the CEO's favourite colour?", "m2"))["response_type"] == "abstain"
