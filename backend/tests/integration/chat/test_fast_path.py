"""S17.10 fast path inside the real turn graph (fake provider, mocked reranker, test database)."""

import uuid
from collections.abc import Callable
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.support.ledger_env import ledger_owners
from tests.support.turn_env import answer_script, install_runner, new_session, post, seed_chunk

__all__ = ["ledger_owners"]  # fixture import

Q = "Services twelve months"
ONE = (("Twelve months.", ["E1.0"]),)


async def steps_of(sm: async_sessionmaker[AsyncSession], owner_id: uuid.UUID) -> list[Any]:
    async with sm() as s:
        rows = await s.execute(
            text(
                "SELECT st.node, st.status, st.reason_code, st.duration_ms FROM run_steps st "
                "JOIN rag_runs r ON r.id = st.run_id WHERE r.owner_id = :o ORDER BY r.created_at, st.seq"
            ),
            {"o": owner_id},
        )
        return list(rows)


async def test_one_chat_call(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    o = await ledger_owners()
    await seed_chunk(sm, o.id)
    script = answer_script(Q, *ONE)[1:]  # no analysis call is scripted: the fast path skips it
    prov = install_runner(api, sm, script, fast_path=True)
    r = await post(api, o, await new_session(api, o), Q, "m1")
    assert r.status_code == 200 and r.json()["message"]["response_type"] == "answer"
    assert len(prov.requests) == 2  # generate + support check (the answer is cited)


async def test_second_turn_analyzes(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    o = await ledger_owners()
    await seed_chunk(sm, o.id)
    prov = install_runner(api, sm, [*answer_script(Q, *ONE)[1:], *answer_script(Q, *ONE)], fast_path=True)
    sid = await new_session(api, o)
    assert (await post(api, o, sid, Q, "m1")).status_code == 200
    assert len(prov.requests) == 2
    r = await post(api, o, sid, Q, "m2")
    assert r.status_code == 200 and r.json()["message"]["response_type"] == "answer"
    assert len(prov.requests) == 5  # the second turn: analyze + generate + support check
    analyze = [s for s in await steps_of(sm, o.id) if s[0] == "analyze"]
    assert [s[1] for s in analyze] == ["skipped", "ok"]


async def test_step_recorded(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    o = await ledger_owners()
    await seed_chunk(sm, o.id)
    install_runner(api, sm, answer_script(Q, *ONE)[1:], fast_path=True)
    r = await post(api, o, await new_session(api, o), Q, "m1")
    assert r.status_code == 200
    steps = {s[0]: s for s in await steps_of(sm, o.id)}
    assert steps["analyze"][1] == "skipped" and steps["analyze"][2] == "FAST_PATH"
    assert steps["analyze"][3] is not None and steps["analyze"][3] >= 0
    assert "route_question" in steps
