"""S17.08 evidence gate inside the real turn graph (fake provider; the mocked reranker scores 1.0 at best)."""

from collections.abc import Callable
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.retrieval.config import GateCfg, RetrievalConfig, get_config
from tests.support.ledger_env import ledger_owners
from tests.support.turn_env import answer_script, install_runner, new_session, post, seed_chunk

__all__ = ["ledger_owners"]  # fixture import

Q = "Services twelve months"
ONE = (("Twelve months.", ["E1.0"]),)


def cfg(floor: float, enabled: bool = True) -> RetrievalConfig:
    return get_config("retrieval@2-rerank").model_copy(
        update={"gate": GateCfg(high=0.0, floor=floor, enabled=enabled)}
    )


async def reasons(sm: async_sessionmaker[AsyncSession], owner_id: Any) -> list[str]:
    async with sm() as s:
        return list(
            (await s.execute(text("SELECT reason_codes FROM rag_runs WHERE owner_id = :o"), {"o": owner_id}))
            .scalars()
            .one()
        )


async def test_below_floor_abstains(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    o = await ledger_owners()
    await seed_chunk(sm, o.id)
    prov = install_runner(api, sm, answer_script(Q, *ONE), cfg=cfg(5.0))
    r = await post(api, o, await new_session(api, o), Q, "m1")
    msg = r.json()["message"]
    assert r.status_code == 200 and msg["response_type"] == "abstain"
    assert len(prov.requests) == 1  # the analysis only: zero generate calls (and no support check)
    assert "ABSTAIN_NO_EVIDENCE" in await reasons(sm, o.id)
    assert msg["content"] == "I could not find anything in your 1 document that answers this."


async def test_above_floor_generates(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    o = await ledger_owners()
    await seed_chunk(sm, o.id)
    prov = install_runner(api, sm, answer_script(Q, *ONE), cfg=cfg(1.0))  # the top score is exactly 1.0
    r = await post(api, o, await new_session(api, o), Q, "m1")
    assert r.status_code == 200 and r.json()["message"]["response_type"] == "answer"
    assert len(prov.requests) == 3  # analyze + generate + support check
    assert "ABSTAIN_NO_EVIDENCE" not in await reasons(sm, o.id)


async def test_bypasses(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    # an identifier match bypasses the floor
    o = await ledger_owners()
    await seed_chunk(sm, o.id)
    async with sm() as s:
        await s.execute(
            text("UPDATE chunks SET tsv_ids = to_tsvector('simple', '30') WHERE owner_id = :o"), {"o": o.id}
        )
        await s.commit()
    q = "notice 30 days"
    prov = install_runner(api, sm, answer_script(q, *ONE), cfg=cfg(5.0))
    r = await post(api, o, await new_session(api, o), q, "m1")
    assert r.status_code == 200 and r.json()["message"]["response_type"] == "answer"
    assert len(prov.requests) == 3

    # full-context mode bypasses the gate
    o2 = await ledger_owners()
    await seed_chunk(sm, o2.id)
    async with sm() as s:  # a known size, or full context treats the scope as too large
        await s.execute(
            text("UPDATE document_versions SET token_count = 1000 WHERE owner_id = :o"), {"o": o2.id}
        )
        await s.commit()
    prov2 = install_runner(api, sm, answer_script(Q, *ONE), cfg=cfg(5.0))
    sid = await new_session(api, o2)
    r2 = await api.post(
        f"/v1/chat/sessions/{sid}/messages",
        json={"content": Q, "client_message_id": "m1", "mode": "full_context"},
        headers=o2.auth,
    )
    assert r2.status_code == 200 and r2.json()["message"]["response_type"] == "answer"
    assert len(prov2.requests) == 3
