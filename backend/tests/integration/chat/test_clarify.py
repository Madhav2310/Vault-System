"""Clarify when ambiguous (fake provider, mocked reranker, test database).

The ambiguity is decided by code: the model only hints, the options are real document titles, a chosen option
narrows the scope by code, and a clarification is never followed by another one."""

import uuid
from collections.abc import Callable
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.llm.providers.fake import structured
from tests.support.ledger_env import ledger_owners
from tests.support.turn_env import (
    EMBED_SLOT,
    analysis,
    answer,
    install_runner,
    new_session,
    parse_sse,
    post,
    seed_chunk,
)

__all__ = ["ledger_owners"]  # fixture import

Q = "What is the notice period?"
ONE = (("Either party may terminate on 30 days notice.", ["E1.1"]),)
TITLES = ("Acme services agreement", "Beta services agreement")


def hint(*titles: str, query: str = Q) -> Any:
    return analysis(
        query,
        intent="clarify",
        clarification={"kind": "document", "question": "Which one, friend?!", "options": list(titles)},
    )


def supported() -> Any:
    return structured({"verdicts": [{"index": 0, "verdict": "supported", "reason": "ok"}]})


async def seed_two(sm: async_sessionmaker[AsyncSession], owner_id: uuid.UUID) -> None:
    for t in TITLES:
        await seed_chunk(sm, owner_id, title=t)


async def pick(
    api: httpx.AsyncClient, owner: Any, sid: uuid.UUID, msg_id: str, option: int, cmid: str
) -> Any:
    return await api.post(
        f"/v1/chat/sessions/{sid}/messages",
        json={"clarify_choice": {"message_id": msg_id, "option": option}, "client_message_id": cmid},
        headers=owner.auth,
    )


async def test_clarify_then_answer(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    o = await ledger_owners()
    await seed_two(sm, o.id)
    script = [hint(*TITLES), EMBED_SLOT, analysis(Q), EMBED_SLOT, answer(*ONE), supported()]
    prov = install_runner(api, sm, script, chat_clarify=True)
    sid = await new_session(api, o)

    r = await post(api, o, sid, Q, "m1", sse=True)
    events = parse_sse(r.text)
    clar = [e for e in events if e["type"] == "clarify"]
    assert len(clar) == 1 and events[-1]["type"] == "done" and events[-1]["response_type"] == "clarify"
    labels = [x["label"] for x in clar[0]["options"]]
    assert sorted(labels) == sorted(TITLES)  # real document titles, nothing the model wrote
    assert "friend" not in clar[0]["question"] and clar[0]["question"] == events[-1]["content"]
    assert len(prov.requests) == 1  # only the analysis call: no answer was written

    hist = (await api.get(f"/v1/chat/sessions/{sid}/messages", headers=o.auth)).json()["items"]
    stored = hist[-1]
    assert stored["response_type"] == "clarify"
    assert sorted(x["label"] for x in stored["clarify_options"]) == sorted(TITLES)

    idx = next(i for i, x in enumerate(stored["clarify_options"]) if x["label"] == TITLES[1])
    r2 = await pick(api, o, sid, stored["id"], idx, "m2")
    assert r2.status_code == 200
    msg = r2.json()["message"]
    assert msg["response_type"] == "answer"
    async with sm() as s:
        titles = {
            row[0]
            for row in await s.execute(
                text(
                    "SELECT DISTINCT d.title FROM run_versions rv "
                    "JOIN document_versions v ON v.id = rv.version_id "
                    "JOIN documents d ON d.id = v.document_id WHERE rv.run_id = :r"
                ),
                {"r": uuid.UUID(r2.json()["run_id"])},
            )
        }
    assert titles == {TITLES[1]}  # the scope was narrowed by code to the chosen document


async def test_no_loop(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    o = await ledger_owners()
    await seed_two(sm, o.id)
    # turn 1 clarifies; turn 2 is a free-text reply and the model hints again, but the answer comes
    reply = "Services twelve months"
    script = [hint(*TITLES), EMBED_SLOT, hint(*TITLES, query=reply), EMBED_SLOT, answer(*ONE), supported()]
    install_runner(api, sm, script, chat_clarify=True)
    sid = await new_session(api, o)
    assert (await post(api, o, sid, Q, "m1")).json()["message"]["response_type"] == "clarify"
    second = await post(api, o, sid, reply, "m2")
    assert second.json()["message"]["response_type"] == "answer"


async def test_no_clarify_when_unambiguous_or_off(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    o = await ledger_owners()
    await seed_two(sm, o.id)
    # no hint from the analysis: two matching documents are not enough
    install_runner(api, sm, [analysis(Q), EMBED_SLOT, answer(*ONE), supported()], chat_clarify=True)
    r = await post(api, o, await new_session(api, o), Q, "m1")
    assert r.json()["message"]["response_type"] == "answer"
    # a hint with the switch off (the default): answered as written
    install_runner(api, sm, [hint(*TITLES), EMBED_SLOT, answer(*ONE), supported()])
    r = await post(api, o, await new_session(api, o), Q, "m2")
    assert r.json()["message"]["response_type"] == "answer"


async def test_pronoun_without_antecedent_clarifies(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    o = await ledger_owners()
    await seed_two(sm, o.id)
    q = "When does it expire?"
    prov = install_runner(api, sm, [analysis(q), EMBED_SLOT], chat_clarify=True)
    r = await post(api, o, await new_session(api, o), q, "m1")
    assert r.json()["message"]["response_type"] == "clarify"
    assert len(prov.requests) == 1


async def test_clarify_choice_is_owner_scoped(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    o = await ledger_owners()
    await seed_two(sm, o.id)
    install_runner(api, sm, [hint(*TITLES), EMBED_SLOT], chat_clarify=True)
    sid = await new_session(api, o)
    msg = (await post(api, o, sid, Q, "m1")).json()["message"]
    other = await ledger_owners()  # negative cross-user test: another owner's session is a 404
    assert (await pick(api, other, sid, msg["id"], 0, "x1")).status_code == 404
    assert (await pick(api, o, sid, str(uuid.uuid4()), 0, "x2")).status_code == 404
    assert (await pick(api, o, sid, msg["id"], 3, "x3")).status_code == 404
