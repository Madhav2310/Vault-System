"""SSE turn stream (S18.01): event order and schema, equality with what is persisted, headers, heartbeat,
parity with JSON mode, disconnect, retry of a finished turn, and the schema endpoint. Fake provider only."""

import asyncio
import contextlib
import json
import uuid
from collections.abc import Callable
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.chat import stream as stream_mod
from app.chat.events import parse_event
from tests.support.ledger_env import ledger_owners
from tests.support.turn_env import (
    answer_script,
    install_runner,
    new_session,
    parse_sse,
    post,
    seed_chunk,
)

__all__ = ["ledger_owners"]  # fixture import

Q = "Services twelve months"
TWO = (("Twelve months.", ["E1.0"]), ("Thirty days notice.", ["E1.1"]))


async def _setup(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient, **kw: Any
) -> tuple[Any, uuid.UUID, uuid.UUID]:
    o = await ledger_owners()
    cid = await seed_chunk(sm, o.id, "Services Agreement")
    install_runner(api, sm, answer_script(Q, *TWO), **kw)
    return o, await new_session(api, o), cid


async def _stream(api: httpx.AsyncClient, o: Any, sid: uuid.UUID, cmid: str = "m1") -> list[dict[str, Any]]:
    r = await post(api, o, sid, Q, cmid, sse=True)
    assert r.status_code == 200, r.text
    return parse_sse(r.text)


async def test_event_order(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    o, sid, _ = await _setup(sm, ledger_owners, api)
    types = [e["type"] for e in await _stream(api, o, sid)]
    assert types[0] == "run.started" and types[-1] == "done"
    assert types.count("done") == 1 and "error" not in types
    assert types.count("status") >= 1 and types.count("segment") == 2
    assert types.index("status") < types.index("segment") < types.index("verification") < types.index("done")
    run_ids = {e["run_id"] for e in await _stream(api, o, sid, "m1")}  # retry: same run
    assert len(run_ids) == 1


async def test_events_validate(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    o, sid, _ = await _setup(sm, ledger_owners, api)
    events = await _stream(api, o, sid)
    parsed = [parse_event(e) for e in events]  # raises on any event outside the union
    assert [p.type for p in parsed] == [e["type"] for e in events]
    stages = [e["stage"] for e in events if e["type"] == "status"]
    assert stages[0] == "analyzing" and "writing" in stages and stages[-1] == "saving"
    assert len(stages) == len(set(stages))  # `status` fires on stage changes only


async def test_stream_equals_persisted(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    o, sid, cid = await _setup(sm, ledger_owners, api)
    events = await _stream(api, o, sid)
    done = events[-1]
    hist = (await api.get(f"/v1/chat/sessions/{sid}/messages", headers=o.auth)).json()["items"]
    msg = hist[-1]
    assert msg["role"] == "assistant" and done["message_id"] == msg["id"]
    assert done["content"] == msg["content"] and done["response_type"] == msg["response_type"] == "answer"
    streamed = [e for e in events if e["type"] == "segment"]
    assert [s["index"] for s in streamed] == [s["ordinal"] for s in msg["segments"]]
    for ev, p in zip(streamed, msg["segments"], strict=True):
        assert (ev["text"], ev["support"]) == (p["text"], p["support"])
        assert len(ev["evidence"]) == len(p["evidence"]) == 1
        for key in (
            "chunk_id", "document_id", "document_title", "version_no", "pages",
            "sentence_start", "sentence_end", "cited_text",
        ):  # fmt: skip
            assert ev["evidence"][0][key] == p["evidence"][0][key], key
    assert streamed[0]["evidence"][0]["chunk_id"] == str(cid)
    assert streamed[0]["evidence"][0]["cited_text"].startswith("The Consultant shall")
    async with sm() as s:
        row = (
            await s.execute(text("SELECT latency_ms FROM rag_runs WHERE owner_id = :o"), {"o": o.id})
        ).one()
    assert done["latency_ms"] == row[0]


async def test_headers(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    o, sid, _ = await _setup(sm, ledger_owners, api)
    r = await post(api, o, sid, Q, "m1", sse=True)
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.headers["x-accel-buffering"] == "no"
    assert r.headers["cache-control"] == "no-cache"


async def test_heartbeat(
    sm: async_sessionmaker[AsyncSession],
    ledger_owners: Callable[[], Any],
    api: httpx.AsyncClient,
    monkeypatch: Any,
) -> None:
    """A slow generation produces heartbeat comments. The interval is patched down (15 s -> 50 ms) instead of
    waiting 16 s of wall clock."""
    monkeypatch.setattr(stream_mod, "HEARTBEAT_S", 0.05)

    async def slow() -> None:
        await asyncio.sleep(0.4)

    o, sid, _ = await _setup(sm, ledger_owners, api, before=slow)
    r = await post(api, o, sid, Q, "m1", sse=True)
    assert r.text.count(": heartbeat\n\n") >= 2
    assert stream_mod.HEARTBEAT_S == 0.05 and [e["type"] for e in parse_sse(r.text)][-1] == "done"


async def test_json_sse_parity(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    o = await ledger_owners()
    await seed_chunk(sm, o.id, "Services Agreement")
    install_runner(api, sm, answer_script(Q, *TWO) + answer_script(Q, *TWO))
    s1, s2 = await new_session(api, o), await new_session(api, o)
    j = await post(api, o, s1, Q, "j1")
    assert j.status_code == 200
    events = parse_sse((await post(api, o, s2, Q, "s1", sse=True)).text)
    msg_json = j.json()["message"]
    done = events[-1]
    assert (done["content"], done["response_type"]) == (msg_json["content"], msg_json["response_type"])
    h1 = (await api.get(f"/v1/chat/sessions/{s1}/messages", headers=o.auth)).json()["items"][-1]
    h2 = (await api.get(f"/v1/chat/sessions/{s2}/messages", headers=o.auth)).json()["items"][-1]

    def norm(m: dict[str, Any]) -> list[Any]:
        return [
            (s["ordinal"], s["text"], s["support"], [(e["cited_text"], e["pages"]) for e in s["evidence"]])
            for s in m["segments"]
        ]

    assert norm(h1) == norm(h2) and h1["content"] == h2["content"]


async def test_disconnect_continues(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    """The client goes away right after `run.started`; the turn still runs to completion and persists."""

    async def slow() -> None:
        await asyncio.sleep(0.3)

    o, sid, _ = await _setup(sm, ledger_owners, api, before=slow)
    app = api._transport.app  # type: ignore[attr-defined]
    body = json.dumps({"content": Q, "client_message_id": "m1"}).encode()
    gone = asyncio.Event()
    sent: list[bytes] = []
    sent_body = False

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
        if msg["type"] == "http.response.body" and b"run.started" in msg.get("body", b""):
            sent.append(msg["body"])
            gone.set()

    hdrs = [(b"content-type", b"application/json"), (b"accept", b"text/event-stream")]
    hdrs += [(k.lower().encode(), v.encode()) for k, v in o.auth.items()]
    scope = {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"}, "http_version": "1.1",
        "method": "POST", "scheme": "http", "path": f"/v1/chat/sessions/{sid}/messages",
        "raw_path": f"/v1/chat/sessions/{sid}/messages".encode(), "query_string": b"",
        "headers": hdrs, "client": ("127.0.0.1", 1), "server": ("test", 80),
    }  # fmt: skip
    with contextlib.suppress(Exception):  # which exception a disconnect surfaces depends on the ASGI stack
        await asyncio.wait_for(app(scope, receive, send), 5)
    assert sent, "the client saw run.started"
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
    assert n == 1
    hist = (await api.get(f"/v1/chat/sessions/{sid}/messages", headers=o.auth)).json()["items"]
    assert [m["response_type"] for m in hist if m["role"] == "assistant"] == ["answer"]


async def test_retry_finished(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    o, sid, _ = await _setup(sm, ledger_owners, api)
    first = await _stream(api, o, sid)
    prov_calls = await api.get(f"/v1/chat/sessions/{sid}/messages", headers=o.auth)
    retry = await _stream(api, o, sid)  # the same client_message_id
    assert [e["type"] for e in retry].count("done") == 1
    assert retry[-1]["message_id"] == first[-1]["message_id"] == prov_calls.json()["items"][-1]["id"]
    assert [e["type"] for e in retry] == ["run.started", "done"]  # nothing re-ran: no status, no segments
    async with sm() as s:
        n = (
            await s.execute(text("SELECT count(*) FROM rag_runs WHERE owner_id = :o"), {"o": o.id})
        ).scalar_one()
    assert n == 1


async def test_schema_endpoint(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    o = await ledger_owners()
    r = await api.get("/v1/chat/events/schema", headers=o.auth)
    assert r.status_code == 200
    schema = r.json()
    mapping = schema["discriminator"]["mapping"]
    assert set(mapping) == {
        "run.started",
        "status",
        "segment",
        "verification",
        "followups",
        "clarify",
        "done",
        "error",
    }
    assert (await api.get("/v1/chat/events/schema")).status_code == 401
