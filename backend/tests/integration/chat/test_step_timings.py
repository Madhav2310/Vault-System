"""S17.10 AC5: every executed node writes `duration_ms`; the retrieve step carries its sub-timings."""

from collections.abc import Callable
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.support.ledger_env import ledger_owners
from tests.support.turn_env import answer_script, install_runner, new_session, post, seed_chunk

__all__ = ["ledger_owners"]  # fixture import

Q = "Services twelve months"


async def test_all_nodes_timed(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    o = await ledger_owners()
    await seed_chunk(sm, o.id)
    install_runner(api, sm, answer_script(Q, ("Twelve months.", ["E1.0"])), fast_path=False)
    r = await post(api, o, await new_session(api, o), Q, "m1")
    assert r.status_code == 200
    async with sm() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT st.node, st.duration_ms, st.decision FROM run_steps st "
                    "JOIN rag_runs r ON r.id = st.run_id WHERE r.owner_id = :o ORDER BY st.seq"
                ),
                {"o": o.id},
            )
        ).all()
    nodes = [n for n, _, _ in rows]
    assert nodes == [
        "load_context",
        "route_question",
        "analyze",
        "plan_context",
        "retrieve",
        "gate",
        "generate",
        "verify",
    ]  # fmt: skip  (persist writes the steps, so its own step is not stored)
    assert all(ms is not None and ms >= 0 for _, ms, _ in rows)
    retrieve = next(d for n, _, d in rows if n == "retrieve")
    for key in ("embed_ms", "vector_ms", "lexical_ms", "rerank_ms", "expand_ms"):
        assert isinstance(retrieve[key], int) and retrieve[key] >= 0, key
