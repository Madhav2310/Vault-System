"""S22.05 compare per-entity quotas and the coverage gate over the real database (fake provider, mocked
reranker): every side is cited or stated missing, at most one repair call per side, flag off = old path."""

import uuid
from collections.abc import Callable
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.chat.compare import COMPARE_SIDE_NOT_FOUND, COMPARE_SIDE_REPAIRED, retrieve_per_doc
from app.explain.templates import side_not_found_text
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


def _verdicts(n: int) -> Any:
    return structured({"verdicts": [{"index": i, "verdict": "supported", "reason": "ok"} for i in range(n)]})


def _body(name: str) -> str:
    return f"The {name} notice period is thirty days."


async def _history(api: httpx.AsyncClient, owner: Any, sid: uuid.UUID) -> list[dict[str, Any]]:
    return (await api.get(f"/v1/chat/sessions/{sid}/messages", headers=owner.auth)).json()["items"]  # type: ignore[no-any-return]


async def _reasons(sm: async_sessionmaker[AsyncSession], owner_id: uuid.UUID) -> list[str]:
    async with sm() as s:
        row = (
            await s.execute(text("SELECT reason_codes FROM rag_runs WHERE owner_id = :o"), {"o": owner_id})
        ).one()
    return list(row.reason_codes)


def _cited_titles(last: dict[str, Any]) -> set[str]:
    return {e["document_title"] for s in last["segments"] for e in s["evidence"]}


async def test_every_side(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    """The model cites two of three documents; one targeted repair call covers the third."""
    o = await ledger_owners()
    for n in ("Alpha", "Beta", "Gamma"):
        await seed_chunk(sm, o.id, f"{n} Agreement", _body(n.lower()))
    first = answer(("Alpha and Beta set thirty days.", ["E1.0", "E2.0"]))
    repair = answer(("Gamma also sets thirty days.", ["E1.0"]))
    prov = install_runner(
        api,
        sm,
        [analysis(Q, intent="compare"), EMBED_SLOT, first, repair, _verdicts(2)],
        chat_compare_quota=True,
    )
    sid = await new_session(api, o)
    r = await post(api, o, sid, Q, "m1")
    assert r.status_code == 200
    last = (await _history(api, o, sid))[-1]
    assert _cited_titles(last) == {"Alpha Agreement", "Beta Agreement", "Gamma Agreement"}
    assert len([q for q in prov.requests if "only for the document" in str(q.messages)]) == 1
    codes = await _reasons(sm, o.id)
    assert COMPARE_SIDE_REPAIRED in codes and COMPARE_SIDE_NOT_FOUND not in codes


async def test_no_evidence_side_is_stated_without_repair(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    o = await ledger_owners()
    await seed_chunk(sm, o.id, "Alpha Agreement", _body("alpha"))
    await seed_chunk(sm, o.id, "Beta Agreement", _body("beta"))
    await seed_chunk(sm, o.id, "Gamma Agreement", "Completely unrelated sentence about parking.")
    seg = ("Alpha and Beta set thirty days.", ["E1.0", "E2.0"])
    prov = install_runner(
        api,
        sm,
        [analysis(Q, intent="compare"), EMBED_SLOT, answer(seg), _verdicts(1)],
        chat_compare_quota=True,
    )
    sid = await new_session(api, o)
    assert (await post(api, o, sid, Q, "m1")).status_code == 200
    last = (await _history(api, o, sid))[-1]
    assert last["segments"][-1]["text"] == side_not_found_text("Gamma Agreement")
    assert COMPARE_SIDE_NOT_FOUND in await _reasons(sm, o.id)
    assert not [q for q in prov.requests if "only for the document" in str(q.messages)]


async def test_repair_that_cites_nothing_states_the_side(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    """Exactly one repair call; when it cites nothing the template statement closes the answer (no loop)."""
    o = await ledger_owners()
    for n in ("Alpha", "Beta"):
        await seed_chunk(sm, o.id, f"{n} Agreement", _body(n.lower()))
    script = [
        analysis(Q, intent="compare"),
        EMBED_SLOT,
        answer(("Alpha sets thirty days.", ["E1.0"])),
        answer(("Beta is silent.", [])),
        _verdicts(1),
    ]
    prov = install_runner(api, sm, script, chat_compare_quota=True)
    sid = await new_session(api, o)
    assert (await post(api, o, sid, Q, "m1")).status_code == 200
    last = (await _history(api, o, sid))[-1]
    assert last["segments"][-1]["text"] == side_not_found_text("Beta Agreement")
    assert len([q for q in prov.requests if "only for the document" in str(q.messages)]) == 1
    assert COMPARE_SIDE_NOT_FOUND in await _reasons(sm, o.id)


async def test_quota_respected(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any]
) -> None:
    o = await ledger_owners()
    big = await seed_document(sm, o.id, title="Big")
    for i in range(6):
        await seed_doc_chunk(
            sm, o.id, big, ordinal=i, original_text=f"Big agreement {i} has a notice period."
        )
    for t in ("Two", "Three"):
        d = await seed_document(sm, o.id, title=t)
        await seed_doc_chunk(
            sm, o.id, d, ordinal=0, original_text=f"The {t} agreement notice period is 30 days."
        )
    gw, _ = fake_gateway([EMBED_SLOT])
    retriever = Retriever(QueryEmbedder(gw, None), rerank_client(), None)
    async with sm() as s:
        scope = await compute_scope(s, o.id)
    res = await retrieve_per_doc(retriever, sm, o.id, scope, Q, quota=True)
    async with sm() as s:
        rows = await s.execute(
            text("SELECT document_id FROM chunks WHERE id = ANY(:i)"), {"i": list(res.selected)}
        )
        per_doc: dict[uuid.UUID, int] = {}
        for (d,) in rows:
            per_doc[d] = per_doc.get(d, 0) + 1
    assert len(per_doc) == 3 and max(per_doc.values()) <= 3
    assert (
        per_doc[big.document_id] == 3
    )  # the big document gets exactly its quota, the others keep what they have


async def test_flag_off_is_the_old_path(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    o = await ledger_owners()
    for n in ("Alpha", "Beta"):
        await seed_chunk(sm, o.id, f"{n} Agreement", _body(n.lower()))
    script = [
        analysis(Q, intent="compare"),
        EMBED_SLOT,
        answer(("Alpha sets thirty days.", ["E1.0"])),
        _verdicts(1),
    ]
    prov = install_runner(api, sm, script)  # no chat_compare_quota
    sid = await new_session(api, o)
    assert (await post(api, o, sid, Q, "m1")).status_code == 200
    assert not [q for q in prov.requests if "only for the document" in str(q.messages)]
    codes = await _reasons(sm, o.id)
    assert COMPARE_SIDE_REPAIRED not in codes and COMPARE_SIDE_NOT_FOUND not in codes
    async with sm() as s:
        cfg = (
            await s.execute(text("SELECT config FROM rag_runs WHERE owner_id = :o"), {"o": o.id})
        ).scalar_one()
    assert "answer_compare@1" in cfg["prompts"]
