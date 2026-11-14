"""S17.06 a turn records the default retrieval config (retrieval@2) in rag_runs.config."""

from collections.abc import Callable
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.support.ledger_env import ledger_owners
from tests.support.turn_env import answer_script, install_runner, new_session, post, seed_chunk

__all__ = ["ledger_owners"]  # fixture import

Q = "Services twelve months"


async def test_retrieval_version_recorded(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    o = await ledger_owners()
    await seed_chunk(sm, o.id)
    install_runner(api, sm, answer_script(Q, ("Twelve months.", ["E1.0"])), fast_path=True)
    r = await post(api, o, await new_session(api, o), Q, "m1")
    assert r.status_code == 200
    async with sm() as s:
        row = await s.execute(text("SELECT config FROM rag_runs WHERE owner_id = :o"), {"o": o.id})
        cfg = row.scalar_one()
    assert cfg["retrieval"] == "retrieval@2"
