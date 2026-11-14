"""Client_message_id dedupe."""

import asyncio
import uuid
from collections.abc import Callable
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.chat import sessions as svc
from app.core.ids import uuid7


async def _session(api: httpx.AsyncClient, owner: Any) -> str:
    return str((await api.post("/v1/chat/sessions", json={}, headers=owner.auth)).json()["id"])


def _post(api: httpx.AsyncClient, owner: Any, sid: str, cmid: str, content: str = "hello") -> Any:
    return api.post(
        f"/v1/chat/sessions/{sid}/messages",
        json={"content": content, "client_message_id": cmid},
        headers=owner.auth,
    )


async def _counts(sm: async_sessionmaker[AsyncSession], sid: str) -> tuple[int, int]:
    async with sm() as s:
        m = await s.execute(text("SELECT count(*) FROM chat_messages WHERE session_id = :i"), {"i": sid})
        r = await s.execute(text("SELECT count(*) FROM rag_runs WHERE session_id = :i"), {"i": sid})
        return m.scalar_one(), r.scalar_one()


async def _complete(sm: async_sessionmaker[AsyncSession], owner_id: uuid.UUID, sid: str, run_id: str) -> str:
    """Stand-in for the (not yet built) pipeline's persist + release_turn."""
    aid = uuid7()
    async with sm() as s:
        await s.execute(
            text(
                "INSERT INTO chat_messages (id, owner_id, session_id, role, content, response_type, run_id) "
                "VALUES (:a, :o, :s, 'assistant', 'the answer', 'answer', :r)"
            ),
            {"a": aid, "o": owner_id, "s": sid, "r": run_id},
        )
        await s.execute(
            text(
                "UPDATE rag_runs SET status = 'completed', assistant_message_id = :a, finished_at = now() "
                "WHERE id = :r"
            ),
            {"a": aid, "r": run_id},
        )
        assert await svc.release_turn(s, uuid.UUID(sid), owner_id, uuid.UUID(run_id))
    return str(aid)


async def test_retry_after_completion(
    api: httpx.AsyncClient, owners: Callable[[], Any], sm: async_sessionmaker[AsyncSession]
) -> None:
    o = await owners()
    sid = await _session(api, o)
    first = await _post(api, o, sid, "c1")
    assert first.status_code == 202 and first.json()["deduplicated"] is False
    aid = await _complete(sm, o.id, sid, first.json()["run_id"])
    before = await _counts(sm, sid)
    again = await _post(api, o, sid, "c1")
    assert again.status_code == 200, again.text
    b = again.json()
    assert b["run_id"] == first.json()["run_id"]
    assert b["user_message_id"] == first.json()["user_message_id"]
    assert b["status"] == "completed" and b["deduplicated"] is True
    assert b["message"]["id"] == aid and b["message"]["content"] == "the answer"
    assert await _counts(sm, sid) == before == (2, 1)
    # a new id after completion is a new turn
    assert (await _post(api, o, sid, "c2")).status_code == 202


async def test_retry_while_running(
    api: httpx.AsyncClient, owners: Callable[[], Any], sm: async_sessionmaker[AsyncSession]
) -> None:
    o = await owners()
    sid = await _session(api, o)
    first = await _post(api, o, sid, "c1")
    again = await _post(api, o, sid, "c1")
    assert again.status_code == 202
    assert again.json()["run_id"] == first.json()["run_id"] and again.json()["status"] == "running"
    assert again.json()["deduplicated"] is True
    assert await _counts(sm, sid) == (1, 1)


async def test_concurrent_same_client_message_id(
    api: httpx.AsyncClient, owners: Callable[[], Any], sm: async_sessionmaker[AsyncSession]
) -> None:
    o = await owners()
    sid = await _session(api, o)
    rs = await asyncio.gather(*(_post(api, o, sid, "same") for _ in range(6)))
    assert {r.status_code for r in rs} == {202}
    assert len({r.json()["run_id"] for r in rs}) == 1
    assert sum(1 for r in rs if r.json()["deduplicated"] is False) == 1
    assert await _counts(sm, sid) == (1, 1)


async def test_same_id_different_content_422(api: httpx.AsyncClient, owners: Callable[[], Any]) -> None:
    o = await owners()
    sid = await _session(api, o)
    assert (await _post(api, o, sid, "c1", "one")).status_code == 202
    r = await _post(api, o, sid, "c1", "two")
    assert r.status_code == 422 and r.json()["code"] == "idempotency_key_reused"


async def test_client_message_id_scoped_per_session(
    api: httpx.AsyncClient, owners: Callable[[], Any]
) -> None:
    o = await owners()
    s1, s2 = await _session(api, o), await _session(api, o)
    a, b = await _post(api, o, s1, "c1"), await _post(api, o, s2, "c1")
    assert a.status_code == b.status_code == 202 and a.json()["run_id"] != b.json()["run_id"]


async def test_required(api: httpx.AsyncClient, owners: Callable[[], Any]) -> None:
    o = await owners()
    sid = await _session(api, o)
    r = await api.post(f"/v1/chat/sessions/{sid}/messages", json={"content": "hi"}, headers=o.auth)
    assert r.status_code == 422 and r.json()["code"] == "validation_error"
    r = await api.post(
        f"/v1/chat/sessions/{sid}/messages", json={"content": "hi", "client_message_id": ""}, headers=o.auth
    )
    assert r.status_code == 422
