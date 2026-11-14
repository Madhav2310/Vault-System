"""A client that drops the SSE connection mid-turn cannot wedge the session, duplicate the turn, or
leave the model call unfinished (FakeProvider only; free)."""

import asyncio
import contextlib
import json
import uuid
from collections.abc import Callable
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.support.turn_env import answer_script, install_runner, new_session, post, seed_chunk

Q = "Services twelve months"
ONE = (("Twelve months.", ["E1.0"]),)


async def _drop_after_first_event(api: httpx.AsyncClient, owner: Any, sid: uuid.UUID, cmid: str) -> bytes:
    """POST a streaming turn and vanish as soon as the first event frame is written."""
    app = api._transport.app  # type: ignore[attr-defined]
    body = json.dumps({"content": Q, "client_message_id": cmid}).encode()
    gone, sent_body = asyncio.Event(), False
    seen: list[bytes] = []

    async def receive() -> dict[str, Any]:
        nonlocal sent_body
        if not sent_body:
            sent_body = True
            return {"type": "http.request", "body": body, "more_body": False}
        await gone.wait()
        return {"type": "http.disconnect"}

    async def send(msg: dict[str, Any]) -> None:
        if gone.is_set():
            raise OSError("client disconnected")
        if msg["type"] == "http.response.body" and b"data:" in msg.get("body", b""):
            seen.append(msg["body"])
            gone.set()

    path = f"/v1/chat/sessions/{sid}/messages"
    hdrs = [(b"content-type", b"application/json"), (b"accept", b"text/event-stream")]
    hdrs += [(k.lower().encode(), v.encode()) for k, v in owner.auth.items()]
    scope = {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"}, "http_version": "1.1",
        "method": "POST", "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": b"",
        "headers": hdrs, "client": ("127.0.0.1", 1), "server": ("test", 80),
    }  # fmt: skip
    with contextlib.suppress(Exception):  # which exception a disconnect surfaces depends on the ASGI stack
        await asyncio.wait_for(app(scope, receive, send), 5)
    return b"".join(seen)


async def _wait_assistant(sm: async_sessionmaker[AsyncSession], sid: uuid.UUID) -> int:
    n = 0
    for _ in range(100):
        async with sm() as s:
            n = (
                await s.execute(
                    text("SELECT count(*) FROM chat_messages WHERE session_id = :i AND role = 'assistant'"),
                    {"i": sid},
                )
            ).scalar_one()
        if n:
            break
        await asyncio.sleep(0.05)
    return int(n)


async def test_disconnect_then_retry_and_next_turn(
    api: httpx.AsyncClient, sm: async_sessionmaker[AsyncSession], sec_owners: Callable[[], Any]
) -> None:
    o = await sec_owners()
    await seed_chunk(sm, o.id, "Services Agreement")

    async def slow() -> None:
        await asyncio.sleep(0.2)

    # one scripted turn; a second turn in the same session is scripted too
    prov = install_runner(api, sm, answer_script(Q, *ONE) + answer_script(Q, *ONE), before=slow)
    sid = await new_session(api, o)

    first_frame = await _drop_after_first_event(api, o, sid, "drop-1")
    assert b"run.started" in first_frame
    assert await _wait_assistant(sm, sid) == 1  # the turn finished although nobody was listening
    calls_after_first = len(prov.requests)

    # retry of the same client_message_id: same run, stored answer, no second model call, no new rows
    r = await post(api, o, sid, Q, "drop-1")
    assert r.status_code == 200 and r.json()["deduplicated"] is True
    assert len(prov.requests) == calls_after_first
    async with sm() as s:
        runs = (
            await s.execute(text("SELECT count(*) FROM rag_runs WHERE session_id = :i"), {"i": sid})
        ).scalar_one()
    assert runs == 1

    # the session lease was released: a different message is not refused with turn_in_progress
    r2 = await post(api, o, sid, Q, "drop-2")
    assert r2.status_code == 200, r2.text
    assert r2.json()["run_id"] != r.json()["run_id"]


async def test_disconnect_of_a_foreign_session_stream_leaks_nothing(
    api: httpx.AsyncClient, sm: async_sessionmaker[AsyncSession], sec_owners: Callable[[], Any]
) -> None:
    """The 404 for another owner's session is decided before any stream starts (no partial SSE body)."""
    a, b = await sec_owners(), await sec_owners()
    install_runner(api, sm, answer_script(Q, *ONE))
    sid = await new_session(api, a)
    r = await post(api, b, sid, Q, "x-1", sse=True)
    assert r.status_code == 404 and "text/event-stream" not in r.headers.get("content-type", "")
    assert "data:" not in r.text
