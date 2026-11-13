"""S17.07 whole-document answering over the real database: fake provider, mocked reranker, POST /messages."""

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
from app.llm.providers.fake import FakeProvider, structured
from app.retrieval.query_embed import QueryEmbedder
from app.retrieval.rerank import RerankClient
from app.retrieval.retrieve import Retriever
from tests.integration.chat.test_turn_e2e import EMBED_SLOT, _analysis
from tests.support.chat_gen import fake_gateway
from tests.support.docs import seed_document
from tests.support.ledger_env import ledger_owners

__all__ = ["ledger_owners"]  # fixture import


def _rerank() -> RerankClient:
    def handler(request: httpx.Request) -> httpx.Response:
        n = len(json.loads(request.content)["passages"])
        return httpx.Response(200, json={"scores": [1.0 - i / 100 for i in range(n)], "model": "m"})

    return RerankClient("http://reranker.test", 800, transport=httpx.MockTransport(handler))


async def _seed(
    sm: async_sessionmaker[AsyncSession], owner_id: uuid.UUID, bodies: list[str], *, tokens: int = 1000
) -> list[uuid.UUID]:
    """One document; `bodies[i]` is the chunk with ordinal i, inserted in reverse order. Returns chunk ids."""
    doc = await seed_document(sm, owner_id, title="Agreement")
    ids = [uuid7() for _ in bodies]
    async with sm() as s:
        for i in reversed(range(len(bodies))):
            sents = [list(se) for se in split_sentences(bodies[i])]
            await s.execute(
                text(
                    "INSERT INTO chunks (id, owner_id, document_id, version_id, ordinal, kind, "
                    "original_text, "
                    "embedding_text, page_start, page_end, section_path, sentences, spans, token_count, "
                    "content_hash) VALUES (:id, :o, :d, :v, :ord, 'text', :t, :t, 1, 1, '{}', "
                    "CAST(:sent AS jsonb), CAST('[]' AS jsonb), 20, :h)"
                ),
                {"id": ids[i], "o": owner_id, "d": doc.document_id, "v": doc.version_id, "ord": i,
                 "t": bodies[i], "sent": json.dumps(sents), "h": f"h{i}"},
            )  # fmt: skip
        await s.execute(
            text("UPDATE document_versions SET token_count = :t WHERE id = :v"),
            {"t": tokens, "v": doc.version_id},
        )
        await s.commit()
    return ids


async def _ask(
    sm: async_sessionmaker[AsyncSession],
    api: httpx.AsyncClient,
    owner: Any,
    script: list[Any],
    mode: str,
    max_tokens: int | None = None,
) -> tuple[httpx.Response, FakeProvider, uuid.UUID]:
    gw, prov = fake_gateway(script, ledger=DbLedgerWriter(sm))
    if max_tokens is not None:
        gw.settings.full_context_max_tokens = max_tokens
    deps = TurnDeps(sm, gw, Retriever(QueryEmbedder(gw, None), _rerank(), None))

    async def runner(*a: Any) -> None:
        await run_turn(deps, *a)

    api._transport.app.dependency_overrides[get_turn_runner] = lambda: runner  # type: ignore[attr-defined]
    sid = uuid.UUID((await api.post("/v1/chat/sessions", json={}, headers=owner.auth)).json()["id"])
    r = await api.post(
        f"/v1/chat/sessions/{sid}/messages",
        json={"content": "What does it say?", "client_message_id": "m1", "mode": mode},
        headers=owner.auth,
    )
    return r, prov, sid


async def _run_row(sm: async_sessionmaker[AsyncSession], owner_id: uuid.UUID) -> Any:
    async with sm() as s:
        return (
            await s.execute(
                text("SELECT id, mode, reason_codes FROM rag_runs WHERE owner_id = :o"), {"o": owner_id}
            )
        ).one()


async def test_all_chunks_in_order(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    o = await ledger_owners()
    bodies = [
        "Alpha clause one. Alpha clause two.",
        "Beta clause one.",
        "Gamma clause one. Gamma clause two.",
    ]
    ids = await _seed(sm, o.id, bodies)
    script = [
        _analysis("What does it say?"),
        EMBED_SLOT,
        structured({"status": "answer", "segments": [{"text": "Beta.", "evidence": ["E2.0"]}]}),
    ]
    r, prov, _sid = await _ask(sm, api, o, script, "full_context")
    assert r.status_code == 200, r.text
    assert r.json()["message"]["response_type"] == "answer"
    # one analyze call and one generate call: no retrieval, no rerank
    assert len(prov.requests) == 2
    prompt = prov.requests[-1].messages[0]["content"]
    pos = [prompt.index(w) for w in ("Alpha clause one.", "Beta clause one.", "Gamma clause one.")]
    assert pos == sorted(pos)  # ordinal order, although inserted in reverse
    assert prompt.count("[E") >= 3 and "E3.1 Gamma clause two." in prompt
    run = await _run_row(sm, o.id)
    assert run[1] == "full_context" and "FULL_CONTEXT_TOO_LARGE" not in run[2]
    async with sm() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT chunk_id, selected, result_index, vector_rank, lexical_rank "
                    "FROM retrieval_candidates WHERE run_id = :r ORDER BY result_index"
                ),
                {"r": run[0]},
            )
        ).all()
    assert [x[0] for x in rows] == ids
    assert all(x[1] and x[3] is None and x[4] is None for x in rows)
    assert [x[2] for x in rows] == [0, 1, 2]


async def test_string_handles_over_limit(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    o = await ledger_owners()
    body = " ".join(f"Clause {i} applies to the Services." for i in range(1100))
    await _seed(sm, o.id, [body], tokens=20_000)
    script = [
        _analysis("What does it say?"),
        EMBED_SLOT,
        structured(
            {
                "status": "answer",
                "segments": [{"text": "It applies.", "evidence": ["E1.1050", "E9.9"]}],  # E9.9 is unknown
            }
        ),
    ]
    r, prov, sid = await _ask(sm, api, o, script, "full_context")
    assert r.status_code == 200, r.text
    assert r.json()["message"]["response_type"] == "answer"
    schema = json.dumps(prov.requests[-1].output_schema)
    assert "E1.1050" not in schema and "E1.0" not in schema  # plain strings, no handle enum
    run = await _run_row(sm, o.id)
    assert run[1] == "full_context" and "CITATION_UNRESOLVED" in run[2]
    h = (await api.get(f"/v1/chat/sessions/{sid}/messages", headers=o.auth)).json()["items"]
    ev = h[1]["segments"][0]["evidence"]
    assert len(ev) == 1 and ev[0]["cited_text"].startswith("Clause 1050 applies")


async def test_too_large_falls_back(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    o = await ledger_owners()
    await _seed(sm, o.id, ["The Consultant shall provide the Services."], tokens=80_000)
    script = [
        _analysis("What does it say?"),
        EMBED_SLOT,
        structured({"status": "answer", "segments": [{"text": "Services.", "evidence": ["E1.0"]}]}),
    ]
    r, _prov, _sid = await _ask(sm, api, o, script, "full_context")
    assert r.status_code == 200, r.text
    run = await _run_row(sm, o.id)
    assert run[1] == "rag" and "FULL_CONTEXT_TOO_LARGE" in run[2]
    async with sm() as s:
        steps = (
            (await s.execute(text("SELECT node FROM run_steps WHERE run_id = :r"), {"r": run[0]}))
            .scalars()
            .all()
        )
    assert "retrieve" in steps  # the RAG path ran


async def test_prompt_guard_falls_back(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    """Stored token_count fits, but the assembled prompt (handles, headers) exceeds budget + 4,000."""
    o = await ledger_owners()
    body = " ".join(f"Clause {i} applies to the Services." for i in range(800))
    await _seed(sm, o.id, [body], tokens=50)
    script = [
        _analysis("What does it say?"),
        EMBED_SLOT,
        structured({"status": "answer", "segments": [{"text": "Services.", "evidence": ["E1.0"]}]}),
    ]
    r, _, _ = await _ask(sm, api, o, script, "full_context", max_tokens=100)
    assert r.status_code == 200, r.text
    run = await _run_row(sm, o.id)
    assert run[1] == "rag" and "FULL_CONTEXT_TOO_LARGE" in run[2]
