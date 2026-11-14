"""Chat-run reaper (F-041)."""

import asyncio
from collections.abc import Callable
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.chat.reaper import TURN_ABANDONED, reap_abandoned_runs
from tests.support.ledger_env import World, ledger_owners, make_world

__all__ = ["ledger_owners"]  # fixture import


async def _expire(sm: async_sessionmaker[AsyncSession], w: World, seconds: int) -> None:
    async with sm() as s:
        await s.execute(
            text(
                "UPDATE chat_sessions SET run_lease_expires_at = now() - make_interval(secs => :n) "
                "WHERE id = :s"
            ),
            {"n": seconds, "s": w.session_id},
        )
        await s.commit()


async def _run(sm: async_sessionmaker[AsyncSession], w: World) -> Any:
    async with sm() as s:
        return (
            (
                await s.execute(
                    text("SELECT status, reason_codes, assistant_message_id FROM rag_runs WHERE id = :r"),
                    {"r": w.run_id},
                )
            )
            .mappings()
            .one()
        )


async def test_abandoned_finished(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any]
) -> None:
    owner = await ledger_owners()
    dead = await make_world(sm, owner)
    live = await make_world(sm, await ledger_owners())
    recent = await make_world(sm, await ledger_owners())
    await _expire(sm, dead, 3600)
    await _expire(sm, recent, 5)  # expired, but inside the grace period
    # live: lease from start_turn is still in the future

    reaped = await asyncio.gather(reap_abandoned_runs(sm), reap_abandoned_runs(sm))
    assert sum(reaped) >= 1  # other abandoned rows in a shared test DB may add to the count

    r = await _run(sm, dead)
    assert r["status"] == "failed" and r["reason_codes"] == [TURN_ABANDONED]
    assert r["assistant_message_id"] is not None
    async with sm() as s:
        n = (
            await s.execute(
                text("SELECT count(*) FROM chat_messages WHERE run_id = :r AND role = 'assistant'"),
                {"r": dead.run_id},
            )
        ).scalar_one()
        lease = (
            await s.execute(
                text("SELECT active_run_id FROM chat_sessions WHERE id = :s"), {"s": dead.session_id}
            )
        ).scalar_one()
    assert n == 1, "two concurrent reapers finish the run exactly once"
    assert lease is None, "lease released"
    assert (await _run(sm, live))["status"] == "running"
    assert (await _run(sm, recent))["status"] == "running"
    assert await reap_abandoned_runs(sm) >= 0
