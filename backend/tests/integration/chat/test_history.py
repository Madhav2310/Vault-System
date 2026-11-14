"""Message history endpoint."""

import uuid
from collections.abc import Callable
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.chat import sessions as svc
from app.chat.ledger import persist_turn
from tests.support.ledger_env import World, ledger_owners, make_world, record

__all__ = ["ledger_owners"]  # fixture import


async def _more_turns(sm: async_sessionmaker[AsyncSession], w: World, n: int) -> None:
    """Persist turn 1 (the world's), then n-1 further turns in the same session."""
    async with sm() as s:
        await persist_turn(s, w.owner.id, record(w))
    for i in range(2, n + 1):
        async with sm() as s:
            t = await svc.start_turn(s, w.owner.id, w.session_id, f"Q{i}?", f"m{i}")
        async with sm() as s:
            await persist_turn(s, w.owner.id, record(w, run_id=t.run_id))


async def test_history_shape(
    api: httpx.AsyncClient, sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any]
) -> None:
    w = await make_world(sm, await ledger_owners())
    await _more_turns(sm, w, 1)
    r = await api.get(f"/v1/chat/sessions/{w.session_id}/messages", headers=w.owner.auth)
    assert r.status_code == 200
    body = r.json()
    assert body["next_cursor"] is None
    user, asst = body["items"]
    assert [user["role"], asst["role"]] == ["user", "assistant"]
    assert user["content"] == "Q?" and user["segments"] == [] and user["staleness"] is None
    assert asst["run_id"] == str(w.run_id) and asst["staleness"] is None
    assert asst["coverage_state"] == "covered" and asst["response_type"] == "answer"
    s0, s1 = asst["segments"]
    assert (s0["ordinal"], s0["support"], s1["ordinal"], s1["support"]) == (0, "cited", 1, "none")
    assert s1["evidence"] == []
    assert s0["evidence"] == [
        {
            "chunk_id": str(w.chunk_id),
            "document_id": str(w.doc.document_id),
            "document_title": "Master Agreement",
            "version_id": str(w.doc.version_id),
            "version_no": 1,
            "pages": [2, 3],
            "section_path": ["1", "Scope"],
            "sentence_start": 0,
            "sentence_end": 1,
            "cited_text": "Alpha beta.",
            "deleted": False,
        }
    ]


async def test_pagination(
    api: httpx.AsyncClient, sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any]
) -> None:
    w = await make_world(sm, await ledger_owners())
    await _more_turns(sm, w, 3)  # 6 messages
    url = f"/v1/chat/sessions/{w.session_id}/messages"
    full = (await api.get(url, headers=w.owner.auth, params={"limit": 100})).json()["items"]
    assert len(full) == 6
    assert [m["role"] for m in full] == ["user", "assistant"] * 3
    seen: list[list[str]] = []
    cursor: str | None = None
    while True:
        params: dict[str, Any] = {"limit": 4}
        if cursor:
            params["cursor"] = cursor
        page = (await api.get(url, headers=w.owner.auth, params=params)).json()
        seen.append([m["id"] for m in page["items"]])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    # newest page first; each page is chronological; concatenating oldest page first rebuilds the history
    assert [len(p) for p in seen] == [4, 2]
    assert [i for p in reversed(seen) for i in p] == [m["id"] for m in full]
    # stable: a new turn does not shift the older page behind an existing cursor
    first = (await api.get(url, headers=w.owner.auth, params={"limit": 4})).json()
    async with sm() as s:
        t = await svc.start_turn(s, w.owner.id, w.session_id, "Q4?", "m4")
    async with sm() as s:
        await persist_turn(s, w.owner.id, record(w, run_id=t.run_id))
    older = (
        await api.get(url, headers=w.owner.auth, params={"limit": 4, "cursor": first["next_cursor"]})
    ).json()
    assert [m["id"] for m in older["items"]] == seen[1]
    bad = await api.get(url, headers=w.owner.auth, params={"cursor": "!!"})
    assert bad.status_code == 422 or bad.status_code == 400


async def test_cross_owner_404(
    api: httpx.AsyncClient, sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any]
) -> None:
    w = await make_world(sm, await ledger_owners())
    await _more_turns(sm, w, 1)
    other = await ledger_owners()
    r = await api.get(f"/v1/chat/sessions/{w.session_id}/messages", headers=other.auth)
    assert r.status_code == 404
    r = await api.get(f"/v1/chat/sessions/{uuid.uuid4()}/messages", headers=w.owner.auth)
    assert r.status_code == 404
    async with sm() as s:  # sanity: the data exists for the real owner
        n = (
            await s.execute(
                text("SELECT count(*) FROM chat_messages WHERE session_id = :s"), {"s": w.session_id}
            )
        ).scalar_one()
    assert n == 2
