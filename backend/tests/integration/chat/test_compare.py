"""Compare across documents over the real database: per-document retrieval, both sides cited,
a code-checked "not found" segment and the more-than-5-documents fallback (fake provider, mocked reranker)."""

import uuid
from collections.abc import Callable
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.chat.compare import COMPARE_SIDE_MISSING, COMPARE_TOO_MANY, per_doc_passages, retrieve_per_doc
from app.llm.providers.fake import structured
from app.retrieval.query_embed import QueryEmbedder
from app.retrieval.retrieve import Retriever
from app.retrieval.scope import compute_scope
from tests.integration.chat.conftest import owners
from tests.support.chat_gen import fake_gateway
from tests.support.docs import seed_chunk as seed_doc_chunk
from tests.support.docs import seed_document
from tests.support.ledger_env import ledger_owners
from tests.support.turn_env import (
    EMBED_SLOT,
    analysis,
    answer,
    install_runner,
    new_session,
    post,
    rerank_client,
    seed_chunk,
)

__all__ = ["ledger_owners", "owners"]  # fixture imports

Q = "notice period"
VERDICT = structured({"verdicts": [{"index": 0, "verdict": "supported", "reason": "ok"}]})


def _body(name: str) -> str:
    return f"The {name} notice period is thirty days."


async def _run(sm: async_sessionmaker[AsyncSession], owner_id: uuid.UUID) -> Any:
    async with sm() as s:
        return (
            await s.execute(
                text("SELECT id, route, reason_codes, config FROM rag_runs WHERE owner_id = :o"),
                {"o": owner_id},
            )
        ).one()


async def test_per_doc_candidates(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any]
) -> None:
    """A document with one matching chunk is not crowded out by a document with six."""
    o = await ledger_owners()
    big = await seed_document(sm, o.id, title="Big")
    for i in range(6):
        await seed_doc_chunk(
            sm,
            o.id,
            big,
            ordinal=i,
            original_text=f"Big agreement number {i} has a notice period of {i} days.",
        )
    small = await seed_chunk(sm, o.id, "Small", "The small agreement notice period is thirty days.")
    gw, _ = fake_gateway([EMBED_SLOT])
    retriever = Retriever(QueryEmbedder(gw, None), rerank_client(), None)
    async with sm() as s:
        scope = await compute_scope(s, o.id)
    res = await retrieve_per_doc(retriever, sm, o.id, scope, Q)
    assert small in res.selected
    docs: dict[uuid.UUID, int] = {}
    async with sm() as s:
        rows = await s.execute(
            text("SELECT id, document_id FROM chunks WHERE id = ANY(:i)"), {"i": list(res.selected)}
        )
        for _, d in rows:
            docs[d] = docs.get(d, 0) + 1
    assert len(docs) == 2 and max(docs.values()) <= per_doc_passages(2)
    assert all(c.selected == (c.chunk_id in res.selected) for c in res.candidates)


async def test_both_sides_cited(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    o = await ledger_owners()
    await seed_chunk(sm, o.id, "Alpha Agreement", _body("alpha"))
    await seed_chunk(sm, o.id, "Beta Agreement", _body("beta"))
    seg = ("Both agreements set a thirty day notice period.", ["E1.0", "E2.0"])
    verdicts = structured({"verdicts": [{"index": 0, "verdict": "supported", "reason": "ok"}]})
    install_runner(api, sm, [analysis(Q, intent="compare"), EMBED_SLOT, answer(seg), verdicts])
    sid = await new_session(api, o)
    r = await post(api, o, sid, Q, "m1")
    assert r.status_code == 200
    msg = r.json()
    titles = {
        e["document_title"] for s in (await _history(api, o, sid))[-1]["segments"] for e in s["evidence"]
    }
    assert titles == {"Alpha Agreement", "Beta Agreement"}, msg
    run = await _run(sm, o.id)
    assert run.route == "compare" and "answer_compare@3" in run.config["prompts"]
    async with sm() as s:
        versions = (
            await s.execute(
                text("SELECT count(DISTINCT version_id) FROM run_versions WHERE run_id = :r"), {"r": run.id}
            )
        ).scalar_one()
    assert versions == 2


async def _history(api: httpx.AsyncClient, owner: Any, sid: uuid.UUID) -> list[dict[str, Any]]:
    return (await api.get(f"/v1/chat/sessions/{sid}/messages", headers=owner.auth)).json()["items"]  # type: ignore[no-any-return]


async def test_missing_side_stated(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    o = await ledger_owners()
    await seed_chunk(sm, o.id, "Alpha Agreement", _body("alpha"))
    await seed_chunk(sm, o.id, "Beta Agreement", _body("beta"))
    await seed_chunk(sm, o.id, "Gamma Agreement", "Completely unrelated sentence about parking.")
    seg = ("The alpha and beta agreements set a thirty day notice period.", ["E1.0", "E2.0"])
    install_runner(api, sm, [analysis(Q, intent="compare"), EMBED_SLOT, answer(seg), VERDICT])
    sid = await new_session(api, o)
    r = await post(api, o, sid, Q, "m1")
    assert r.status_code == 200
    last = (await _history(api, o, sid))[-1]
    assert last["segments"][-1]["text"] == "Nothing relevant was found in Gamma Agreement."
    assert last["segments"][-1]["support"] == "none"
    assert COMPARE_SIDE_MISSING in (await _run(sm, o.id)).reason_codes


async def test_too_many_falls_back(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    o = await ledger_owners()
    for i in range(6):
        await seed_chunk(sm, o.id, f"Agreement {i}", _body(f"number{i}"))
    install_runner(
        api, sm, [analysis(Q, intent="compare"), EMBED_SLOT, answer(("Thirty days.", ["E1.0"])), VERDICT]
    )
    sid = await new_session(api, o)
    r = await post(api, o, sid, Q, "m1")
    assert r.status_code == 200
    run = await _run(sm, o.id)
    assert run.route == "qa" and COMPARE_TOO_MANY in run.reason_codes
    assert "answer_rag@12" in run.config["prompts"] and "answer_compare@3" not in run.config["prompts"]
