"""Meta route end to end: parameterized, owner-scoped SQL; answer composed in code; one model call."""

import json
import uuid
from collections.abc import Callable
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.ids import uuid7
from app.insights.brief import EMPTY_PARAMS_HASH, KIND, PROMPT_VERSION
from tests.integration.chat.conftest import owners
from tests.support.ledger_env import ledger_owners
from tests.support.turn_env import EMBED_SLOT, analysis, install_runner, new_session, post, seed_chunk

__all__ = ["ledger_owners", "owners"]  # fixture imports

NONE_OP: dict[str, Any] = {
    "op": "COUNT_DOCUMENTS", "status": None, "category": None, "tag": None, "term": None, "field": None,
    "before": None, "after": None, "document_ref": None, "limit": None,
}  # fmt: skip


def meta(q: str, **op: Any) -> list[Any]:
    return [analysis(q, intent="meta", meta_op={**NONE_OP, **op}), EMBED_SLOT]


async def ask(
    api: httpx.AsyncClient, sm: async_sessionmaker[AsyncSession], owner: Any, q: str, script: list[Any]
) -> tuple[Any, dict[str, Any]]:
    prov = install_runner(api, sm, script)
    sid = await new_session(api, owner)
    r = await post(api, owner, sid, q, "m1")
    assert r.status_code == 200, r.text
    hist = (await api.get(f"/v1/chat/sessions/{sid}/messages", headers=owner.auth)).json()["items"]
    return prov, hist[-1]


async def test_count(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    o = await ledger_owners()
    for t in ("A", "B", "C"):
        await seed_chunk(sm, o.id, t)
    q = "How many documents do I have?"
    prov, msg = await ask(api, sm, o, q, meta(q))
    assert msg["segments"][0]["text"] == "You have 3 documents."
    assert len(prov.requests) == 1  # the analysis only: zero generate calls
    async with sm() as s:
        run = (
            await s.execute(
                text("SELECT route, reason_codes, config FROM rag_runs WHERE owner_id = :o"), {"o": o.id}
            )
        ).one()
    assert run[0] == "meta" and "META_COUNT_DOCUMENTS" in run[1] and "analyze_query@4" in run[2]["prompts"]


async def test_mentioning(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    o = await ledger_owners()
    await seed_chunk(sm, o.id, "Services Agreement")  # the shared BODY mentions "terminate"
    await seed_chunk(sm, o.id, "Other Agreement")
    q = "Which documents mention terminate?"
    _, msg = await ask(api, sm, o, q, meta(q, op="DOCUMENTS_MENTIONING", term="terminate"))
    t = msg["segments"][0]["text"]
    assert t.startswith("2 documents mention 'terminate'") and "Services Agreement" in t and "Other" in t
    q2 = "Which documents mention Delaware?"
    _, msg = await ask(api, sm, o, q2, meta(q2, op="DOCUMENTS_MENTIONING", term="Delaware"))
    assert msg["segments"][0]["text"] == "No documents mention 'Delaware'."


async def _brief(
    sm: async_sessionmaker[AsyncSession],
    owner_id: uuid.UUID,
    chunk_id: uuid.UUID,
    facts: list[tuple[str, str]],
) -> None:
    async with sm() as s:
        v = (
            await s.execute(text("SELECT version_id FROM chunks WHERE id = :c"), {"c": chunk_id})
        ).scalar_one()
        content = {
            "document_type": "x", "category": "contract", "tags": [], "summary": "s", "reasons": [],
            "suggested_questions": [], "partial": False,
            "facts": [
                {
                    "field": f, "value": val, "status": "verified", "reason": None,
                    "evidence": [
                        {
                            "chunk_id": str(chunk_id), "version_id": str(v), "version_no": 1, "pages": [3],
                            "section_path": ["Term"], "sentence_start": 0, "sentence_end": 1,
                            "char_start": 0, "char_end": 60,
                            "cited_text": "The Consultant shall provide the Services for twelve months.",
                        }
                    ],
                }
                for f, val in facts
            ],
        }  # fmt: skip
        await s.execute(
            text(
                "INSERT INTO document_insights (id, owner_id, version_id, kind, params_hash, params, "
                "content, status, prompt_version) VALUES (:i, :o, :v, :k, :h, '{}', CAST(:c AS jsonb), "
                "'done', :pv)"
            ),
            {"i": uuid7(), "o": owner_id, "v": v, "k": KIND, "h": EMPTY_PARAMS_HASH, "c": json.dumps(content),
             "pv": PROMPT_VERSION},
        )  # fmt: skip
        await s.commit()


async def test_key_dates(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    o = await ledger_owners()
    c1 = await seed_chunk(sm, o.id, "Early Agreement")
    c2 = await seed_chunk(sm, o.id, "Late Agreement")
    await _brief(sm, o.id, c1, [("expiry_date", "March 3, 2024"), ("party", "Acme")])
    await _brief(sm, o.id, c2, [("expiry_date", "June 1, 2026")])
    q = "List contracts expiring before 2025"
    _, msg = await ask(api, sm, o, q, meta(q, op="LIST_KEY_DATES", field="expiry_date", before="2025-01-01"))
    segs = msg["segments"]
    assert [s["text"] for s in segs] == ["Early Agreement: expiry date March 3, 2024."]
    ev = segs[0]["evidence"]
    assert len(ev) == 1 and ev[0]["chunk_id"] == str(c1) and ev[0]["document_title"] == "Early Agreement"
    assert ev[0]["cited_text"].startswith("The Consultant shall provide")
    assert segs[0]["support"] == "cited"


async def test_owner_scoped(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    a, b = await ledger_owners(), await ledger_owners()
    await seed_chunk(sm, a.id, "Mine")
    for t in ("Theirs 1", "Theirs 2"):
        cid = await seed_chunk(sm, b.id, t)
    await _brief(sm, b.id, cid, [("expiry_date", "March 3, 2024")])
    q = "How many documents do I have?"
    _, msg = await ask(api, sm, a, q, meta(q))
    assert msg["segments"][0]["text"] == "You have 1 document."
    q = "Which documents mention terminate?"
    _, msg = await ask(api, sm, a, q, meta(q, op="DOCUMENTS_MENTIONING", term="terminate"))
    assert "Theirs" not in msg["segments"][0]["text"] and "Mine" in msg["segments"][0]["text"]
    q = "key dates?"
    _, msg = await ask(api, sm, a, q, meta(q, op="LIST_KEY_DATES"))
    assert msg["segments"][0]["text"] == "No matching dates were found in your documents."
