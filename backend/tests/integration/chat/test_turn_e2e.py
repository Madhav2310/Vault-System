"""Turn orchestrator end to end over the real database: analyze -> retrieve -> generate -> persist, with the
fake provider and a mocked reranker. POST /messages runs the turn and returns the stored assistant message."""

import json
import uuid
from collections.abc import Callable
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import get_turn_runner
from app.chat.turn import TurnDeps, run_turn
from app.core.ids import uuid7
from app.ingestion.sentences import split_sentences
from app.llm.gateway import DbLedgerWriter
from app.llm.providers.fake import refusal, structured
from app.retrieval.query_embed import QueryEmbedder
from app.retrieval.retrieve import Retriever
from tests.support.chat_gen import fake_gateway
from tests.support.docs import seed_document
from tests.support.ledger_env import ledger_owners

__all__ = ["ledger_owners"]  # fixture import

BODY = (
    "The Consultant shall provide the Services for twelve months. "
    "Either party may terminate on 30 days notice."
)

# the fake provider's embed() pops one scripted step: a slot keeps the chat steps in order
EMBED_SLOT = structured({})


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


async def _seed_chunk(sm: async_sessionmaker[AsyncSession], owner_id: uuid.UUID, title: str) -> uuid.UUID:
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


async def _post(
    api: httpx.AsyncClient, owner: Any, sid: uuid.UUID, content: str, cmid: str
) -> httpx.Response:
    return await api.post(
        f"/v1/chat/sessions/{sid}/messages",
        json={"content": content, "client_message_id": cmid},
        headers=owner.auth,
    )


async def test_cited_answer_end_to_end(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    o = await ledger_owners()
    cid = await _seed_chunk(sm, o.id, "Services Agreement")
    q = "Services twelve months"
    gw, prov = fake_gateway(
        [
            _analysis(q),
            EMBED_SLOT,
            structured({"status": "answer", "segments": [{"text": "Twelve months.", "evidence": ["E1.0"]}]}),
            structured({"verdicts": [{"index": 0, "verdict": "supported", "reason": "same fact"}]}),
        ],
        ledger=DbLedgerWriter(sm),
    )
    deps = TurnDeps(sm, gw, Retriever(QueryEmbedder(gw, None), None))

    async def runner(*a: Any) -> None:
        await run_turn(deps, *a)

    api._transport.app.dependency_overrides[get_turn_runner] = lambda: runner  # type: ignore[attr-defined]
    sid = uuid.UUID((await api.post("/v1/chat/sessions", json={}, headers=o.auth)).json()["id"])
    r = await _post(api, o, sid, q, "m1")
    assert r.status_code == 200, r.text
    msg = r.json()["message"]
    assert msg["response_type"] == "answer" and msg["content"] == "Twelve months."
    # history shows the citation, copied by code from the stored sentence
    h = (await api.get(f"/v1/chat/sessions/{sid}/messages", headers=o.auth)).json()["items"]
    assert [m["role"] for m in h] == ["user", "assistant"]
    ev = h[1]["segments"][0]["evidence"][0]
    assert (
        ev["chunk_id"] == str(cid)
        and ev["pages"] == [3, 3]
        and ev["cited_text"].startswith("The Consultant shall")
    )
    async with sm() as s:
        run = (
            await s.execute(
                text(
                    "SELECT status, route, llm_calls_used, config, trace_id FROM rag_runs WHERE owner_id = :o"
                ),
                {"o": o.id},
            )
        ).one()
        assert run[0] == "completed" and run[1] == "qa" and run[2] == 3
        assert run[3]["retrieval"] == "retrieval@2" and "answer_rag@12" in run[3]["prompts"]
        n = (
            await s.execute(
                text(
                    "SELECT count(*) FROM retrieval_candidates c JOIN rag_runs r ON r.id = c.run_id "
                    "WHERE r.owner_id = :o AND c.selected"
                ),
                {"o": o.id},
            )
        ).scalar_one()
        assert n == 1
        lease = (
            await s.execute(text("SELECT active_run_id FROM chat_sessions WHERE id = :i"), {"i": sid})
        ).scalar_one()
        assert lease is None  # released
    # a retried client_message_id returns the stored answer and does not run again
    r2 = await _post(api, o, sid, q, "m1")
    assert r2.status_code == 200 and r2.json()["deduplicated"] and len(prov.requests) == 3


async def test_no_documents_abstains_without_model_call(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    o = await ledger_owners()
    gw, prov = fake_gateway([], ledger=DbLedgerWriter(sm))
    deps = TurnDeps(sm, gw, Retriever(QueryEmbedder(gw, None), None))

    async def runner(*a: Any) -> None:
        await run_turn(deps, *a)

    api._transport.app.dependency_overrides[get_turn_runner] = lambda: runner  # type: ignore[attr-defined]
    sid = uuid.UUID((await api.post("/v1/chat/sessions", json={}, headers=o.auth)).json()["id"])
    r = await _post(api, o, sid, "anything?", "m1")
    assert r.status_code == 200 and r.json()["message"]["response_type"] == "abstain"
    assert prov.requests == []  # no documents -> no model call


async def test_model_failure_becomes_error_response(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:

    o = await ledger_owners()
    await _seed_chunk(sm, o.id, "Doc")
    gw, _ = fake_gateway([_analysis("terminate notice"), EMBED_SLOT, refusal()], ledger=DbLedgerWriter(sm))
    deps = TurnDeps(sm, gw, Retriever(QueryEmbedder(gw, None), None))

    async def runner(*a: Any) -> None:
        await run_turn(deps, *a)

    api._transport.app.dependency_overrides[get_turn_runner] = lambda: runner  # type: ignore[attr-defined]
    sid = uuid.UUID((await api.post("/v1/chat/sessions", json={}, headers=o.auth)).json()["id"])
    r = await _post(api, o, sid, "terminate notice", "m1")
    assert r.status_code == 200 and r.json()["message"]["response_type"] == "error"
    async with sm() as s:
        assert (
            await s.execute(text("SELECT active_run_id FROM chat_sessions WHERE id = :i"), {"i": sid})
        ).scalar_one() is None


async def test_persist_failure_still_finishes_run(
    sm: async_sessionmaker[AsyncSession],
    ledger_owners: Callable[[], Any],
    api: httpx.AsyncClient,
    monkeypatch: Any,
) -> None:
    """A ledger failure on the full record falls back to a minimal error record (run finished)."""
    import app.chat.turn as turn_mod

    real = turn_mod.persist_turn
    calls = {"n": 0}

    async def flaky(db: AsyncSession, owner_id: uuid.UUID, rec: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return await real(db, owner_id, rec)

    monkeypatch.setattr(turn_mod, "persist_turn", flaky)
    o = await ledger_owners()
    await _seed_chunk(sm, o.id, "Doc")
    gw, _ = fake_gateway([_analysis("terminate notice"), EMBED_SLOT, refusal()], ledger=DbLedgerWriter(sm))
    deps = TurnDeps(sm, gw, Retriever(QueryEmbedder(gw, None), None))

    async def runner(*a: Any) -> None:
        await run_turn(deps, *a)

    api._transport.app.dependency_overrides[get_turn_runner] = lambda: runner  # type: ignore[attr-defined]
    sid = uuid.UUID((await api.post("/v1/chat/sessions", json={}, headers=o.auth)).json()["id"])
    r = await _post(api, o, sid, "terminate notice", "m1")
    assert r.status_code == 200 and r.json()["message"]["response_type"] == "error" and calls["n"] == 2
    async with sm() as s:
        row = (
            await s.execute(text("SELECT status FROM rag_runs WHERE owner_id = :o"), {"o": o.id})
        ).scalar_one()
        lease = (
            await s.execute(text("SELECT active_run_id FROM chat_sessions WHERE id = :i"), {"i": sid})
        ).scalar_one()
    assert row == "failed" and lease is None
