"""Turn ownership (CAS with lease). AC4 (no connection held during the generate node) is not testable
yet: there is no generate node until S3.1x."""

import asyncio
import uuid
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.chat import sessions as svc


async def _session(api: httpx.AsyncClient, owner: Any) -> uuid.UUID:
    r = await api.post("/v1/chat/sessions", json={}, headers=owner.auth)
    return uuid.UUID(r.json()["id"])


async def _active(sm: async_sessionmaker[AsyncSession], sid: uuid.UUID) -> uuid.UUID | None:
    async with sm() as s:
        return (
            await s.execute(text("SELECT active_run_id FROM chat_sessions WHERE id = :i"), {"i": sid})
        ).scalar_one()


async def test_second_claim_409(
    api: httpx.AsyncClient, owners: Callable[[], Any], sm: async_sessionmaker[AsyncSession]
) -> None:
    o = await owners()
    sid = await _session(api, o)
    r1, r2 = uuid.uuid4(), uuid.uuid4()
    async with sm() as s:
        await svc.claim_turn(s, sid, o.id, r1)
    async with sm() as s:
        with pytest.raises(svc.TurnInProgress):
            await svc.claim_turn(s, sid, o.id, r2)
    assert await _active(sm, sid) == r1
    # API: a different message while the turn is live -> 409 turn_in_progress
    r = await api.post(
        f"/v1/chat/sessions/{sid}/messages", json={"content": "q", "client_message_id": "m1"}, headers=o.auth
    )
    assert r.status_code == 409 and r.json()["code"] == "turn_in_progress" and r.json()["retryable"] is True


async def test_claim_after_expiry(
    api: httpx.AsyncClient, owners: Callable[[], Any], sm: async_sessionmaker[AsyncSession]
) -> None:
    o = await owners()
    sid = await _session(api, o)
    r1, r2 = uuid.uuid4(), uuid.uuid4()
    async with sm() as s:
        await svc.claim_turn(s, sid, o.id, r1)
        await s.execute(
            text("UPDATE chat_sessions SET run_lease_expires_at = now() - interval '1 second' WHERE id = :i"),
            {"i": sid},
        )
        await s.commit()
    async with sm() as s:
        await svc.claim_turn(s, sid, o.id, r2)
    assert await _active(sm, sid) == r2
    # the displaced run can neither release nor extend
    async with sm() as s:
        assert await svc.release_turn(s, sid, o.id, r1) is False
        assert await svc.extend_lease(s, sid, o.id, r1) is False
    assert await _active(sm, sid) == r2


async def test_fenced_release(
    api: httpx.AsyncClient, owners: Callable[[], Any], sm: async_sessionmaker[AsyncSession]
) -> None:
    o, other = await owners(), await owners()
    sid = await _session(api, o)
    r1, r2 = uuid.uuid4(), uuid.uuid4()
    async with sm() as s:
        await svc.claim_turn(s, sid, o.id, r1)
    async with sm() as s:
        assert await svc.release_turn(s, sid, o.id, r2) is False  # stale/unknown run_id
        assert await svc.release_turn(s, sid, other.id, r1) is False  # wrong owner
    assert await _active(sm, sid) == r1
    async with sm() as s:
        assert await svc.release_turn(s, sid, o.id, r1) is True
    assert await _active(sm, sid) is None
    async with sm() as s:
        await svc.claim_turn(s, sid, o.id, r2)  # free again


async def test_extend_lease(
    api: httpx.AsyncClient, owners: Callable[[], Any], sm: async_sessionmaker[AsyncSession]
) -> None:
    o = await owners()
    sid = await _session(api, o)
    r1 = uuid.uuid4()
    async with sm() as s:
        await svc.claim_turn(s, sid, o.id, r1)
        await s.execute(
            text("UPDATE chat_sessions SET run_lease_expires_at = now() + interval '1 second' WHERE id = :i"),
            {"i": sid},
        )
        await s.commit()
    async with sm() as s:
        assert await svc.extend_lease(s, sid, o.id, r1) is True
        remaining = (
            await s.execute(
                text(
                    "SELECT extract(epoch FROM run_lease_expires_at - now()) FROM chat_sessions WHERE id=:i"
                ),
                {"i": sid},
            )
        ).scalar_one()
    assert 60 < remaining <= svc.LEASE_SECONDS


async def test_concurrent_claims_exactly_one_wins(
    api: httpx.AsyncClient, owners: Callable[[], Any], sm: async_sessionmaker[AsyncSession]
) -> None:
    o = await owners()
    sid = await _session(api, o)

    async def attempt() -> bool:
        async with sm() as s:
            try:
                await svc.claim_turn(s, sid, o.id, uuid.uuid4())
            except svc.TurnInProgress:
                return False
            return True

    results = await asyncio.gather(*(attempt() for _ in range(8)))
    assert results.count(True) == 1


async def test_concurrent_posts_one_409(
    api: httpx.AsyncClient, owners: Callable[[], Any], sm: async_sessionmaker[AsyncSession]
) -> None:
    o = await owners()
    sid = await _session(api, o)

    async def post(i: int) -> httpx.Response:
        return await api.post(
            f"/v1/chat/sessions/{sid}/messages",
            json={"content": f"q{i}", "client_message_id": f"m{i}"},
            headers=o.auth,
        )

    rs = await asyncio.gather(post(1), post(2))
    assert sorted(r.status_code for r in rs) == [202, 409]
    loser = next(r for r in rs if r.status_code == 409)
    assert loser.json()["code"] == "turn_in_progress"
    async with sm() as s:
        n = (
            await s.execute(text("SELECT count(*) FROM chat_messages WHERE session_id = :i"), {"i": sid})
        ).scalar_one()
        runs = (
            await s.execute(text("SELECT count(*) FROM rag_runs WHERE session_id = :i"), {"i": sid})
        ).scalar_one()
    assert n == 1 and runs == 1  # the loser left no rows behind
