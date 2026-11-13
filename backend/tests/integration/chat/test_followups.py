"""Follow-ups end to end (S18.04): stored, returned, SSE event before `done`, abstain from briefs."""

import json
from collections.abc import Callable
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.ids import uuid7
from app.insights.brief import EMPTY_PARAMS_HASH, KIND, PROMPT_VERSION
from tests.integration.chat.conftest import owners
from tests.support.docs import seed_document
from tests.support.ledger_env import ledger_owners
from tests.support.turn_env import (
    EMBED_SLOT,
    analysis,
    answer_script,
    install_runner,
    new_session,
    parse_sse,
    post,
    seed_chunk,
)

__all__ = ["ledger_owners", "owners"]  # fixture imports

Q = "Services twelve months"
RAW = [
    "What is the notice period?",
    "x" * 130,  # over the limit
    "services TWELVE months?",  # repeats the question
    "Who pays the fees?",
    "Can the term be extended?",
    "Is there a penalty?",  # a fourth survivor: over the count
]
KEPT = ["What is the notice period?", "Who pays the fees?", "Can the term be extended?"]


async def _answered(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> tuple[Any, Any]:
    o = await ledger_owners()
    await seed_chunk(sm, o.id, "Services Agreement")
    install_runner(api, sm, answer_script(Q, ("Twelve months.", ["E1.0"]), followups=RAW))
    return o, await new_session(api, o)


async def test_stored_and_returned(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    o, sid = await _answered(sm, ledger_owners, api)
    r = await post(api, o, sid, Q, "m1")
    assert r.status_code == 200 and r.json()["message"]["followups"] == KEPT
    hist = (await api.get(f"/v1/chat/sessions/{sid}/messages", headers=o.auth)).json()["items"]
    assert [m["followups"] for m in hist] == [[], KEPT]  # the user message has none
    assert all(len(f) <= 120 for f in hist[1]["followups"])
    async with sm() as s:
        stored = (
            await s.execute(
                text("SELECT followups FROM chat_messages WHERE session_id = :i AND role = 'assistant'"),
                {"i": sid},
            )
        ).scalar_one()
        used = (
            await s.execute(text("SELECT config FROM rag_runs WHERE owner_id = :o"), {"o": o.id})
        ).scalar_one()
    assert stored == KEPT and "answer_rag@2" in used["prompts"]


async def test_sse_event(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    o, sid = await _answered(sm, ledger_owners, api)
    events = parse_sse((await post(api, o, sid, Q, "m1", sse=True)).text)
    types = [e["type"] for e in events]
    assert types.count("followups") == 1 and types[-1] == "done"
    assert types.index("followups") == len(types) - 2  # right before `done`
    assert events[types.index("followups")]["questions"] == KEPT


async def test_abstain_uses_brief(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    o = await ledger_owners()
    a, b = await seed_document(sm, o.id, title="A"), await seed_document(sm, o.id, title="B")
    async with sm() as s:
        for doc, qs in ((a, ["A1?", "A2?", "A3?"]), (b, ["B1?", "B2?"])):
            await s.execute(
                text(
                    "INSERT INTO document_insights (id, owner_id, version_id, kind, params_hash, status, "
                    "prompt_version, content) VALUES (:i, :o, :v, :k, :h, 'done', :pv, CAST(:c AS jsonb))"
                ),
                {
                    "i": uuid7(), "o": o.id, "v": doc.version_id, "k": KIND, "h": EMPTY_PARAMS_HASH,
                    "pv": PROMPT_VERSION, "c": json.dumps({"suggested_questions": qs}),
                },
            )  # fmt: skip
        await s.commit()
    install_runner(api, sm, [analysis("anything"), EMBED_SLOT])  # no chunks: the evidence gate abstains
    sid = await new_session(api, o)
    r = await post(api, o, sid, "anything", "m1")
    msg = r.json()["message"]
    assert msg["response_type"] == "abstain"
    # round-robin across the two briefs, at most three, none equal to the question
    assert sorted(msg["followups"]) == sorted(["A1?", "B1?", "A2?"]) and len(msg["followups"]) == 3
