"""S14.06 POST /v1/feedback: per-answer rating, idempotent per (message, user), owner-scoped."""

import uuid
from collections.abc import Callable
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from structlog.testing import capture_logs

from app.chat.ledger import persist_turn
from app.feedback.service import feedback_summary
from tests.support.ledger_env import World, make_world, record


async def _turn(sm: async_sessionmaker[AsyncSession], owner: Any) -> tuple[World, uuid.UUID]:
    """A finished turn (two segments: ordinals 0 and 1) and its assistant message id."""
    w = await make_world(sm, owner)
    async with sm() as s:
        persisted = await persist_turn(s, owner.id, record(w))
    return w, persisted.assistant_message_id


async def _rows(sm: async_sessionmaker[AsyncSession], owner_id: uuid.UUID) -> list[Any]:
    async with sm() as s:
        return list(
            (
                await s.execute(
                    text(
                        "SELECT f.kind, f.note, f.segment_id, f.run_id, f.eval_status, a.ordinal "
                        "FROM feedback f LEFT JOIN answer_segments a ON a.id = f.segment_id "
                        "WHERE f.owner_id = :o ORDER BY f.created_at, a.ordinal"
                    ),
                    {"o": owner_id},
                )
            ).all()
        )


async def test_upsert(api: httpx.AsyncClient, sm: Any, fb_owners: Callable[[], Any]) -> None:
    o = await fb_owners()
    w, mid = await _turn(sm, o)
    r = await api.post("/v1/feedback", json={"message_id": str(mid), "rating": "up"}, headers=o.auth)
    assert r.status_code == 200, r.text
    first = r.json()
    assert first["rating"] == "up" and first["reason"] is None and first["segment_ordinal"] is None
    assert len(await _rows(sm, o.id)) == 1

    # the identical request again: still one row, same id
    again = (
        await api.post("/v1/feedback", json={"message_id": str(mid), "rating": "up"}, headers=o.auth)
    ).json()
    assert again["id"] == first["id"] and len(await _rows(sm, o.id)) == 1

    # the last write wins, including the reason and the note
    r = await api.post(
        "/v1/feedback",
        json={"message_id": str(mid), "rating": "down", "reason": "wrong", "note": "the date is off"},
        headers=o.auth,
    )
    assert r.status_code == 200 and r.json()["id"] == first["id"]
    (row,) = await _rows(sm, o.id)
    assert (row.kind, row.note, row.run_id, row.eval_status) == (
        "down:wrong",
        "the date is off",
        w.run_id,
        "new",
    )

    # a segment-level rating is its own row; re-posting it updates that row only
    for rating in ("down", "up"):
        r = await api.post(
            "/v1/feedback",
            json={"message_id": str(mid), "rating": rating, "segment_ordinal": 1},
            headers=o.auth,
        )
        assert r.status_code == 200 and r.json()["segment_ordinal"] == 1
    rows = await _rows(sm, o.id)
    assert sorted((x.ordinal, x.kind) for x in rows if x.ordinal is not None) == [(1, "up")]
    assert len(rows) == 2

    # reason is only meaningful on a down rating
    r = await api.post(
        "/v1/feedback", json={"message_id": str(mid), "rating": "up", "reason": "wrong"}, headers=o.auth
    )
    assert r.status_code == 422
    r = await api.post(
        "/v1/feedback", json={"message_id": str(mid), "rating": "down", "note": "x" * 501}, headers=o.auth
    )
    assert r.status_code == 422
    assert "x" * 50 not in r.text  # validation errors never echo the submitted note


async def test_cross_owner_404(api: httpx.AsyncClient, sm: Any, fb_owners: Callable[[], Any]) -> None:
    a, b = await fb_owners(), await fb_owners()
    _, mid = await _turn(sm, a)
    r = await api.post("/v1/feedback", json={"message_id": str(mid), "rating": "down"}, headers=b.auth)
    assert r.status_code == 404
    assert await _rows(sm, b.id) == [] and await _rows(sm, a.id) == []
    r = await api.post("/v1/feedback", json={"message_id": str(uuid.uuid4()), "rating": "up"}, headers=a.auth)
    assert r.status_code == 404
    # a user message has no answer to rate
    async with sm() as s:
        umsg = (
            await s.execute(
                text("SELECT id FROM chat_messages WHERE owner_id = :o AND role = 'user'"), {"o": a.id}
            )
        ).scalar_one()
    r = await api.post("/v1/feedback", json={"message_id": str(umsg), "rating": "up"}, headers=a.auth)
    assert r.status_code == 404


async def test_segment_validation(api: httpx.AsyncClient, sm: Any, fb_owners: Callable[[], Any]) -> None:
    o = await fb_owners()
    _, mid = await _turn(sm, o)
    for bad in (2, 99):  # the turn has segments 0 and 1
        r = await api.post(
            "/v1/feedback",
            json={"message_id": str(mid), "rating": "down", "segment_ordinal": bad},
            headers=o.auth,
        )
        assert r.status_code == 422 and r.json()["code"] == "validation_error"
    r = await api.post(
        "/v1/feedback", json={"message_id": str(mid), "rating": "down", "segment_ordinal": -1}, headers=o.auth
    )
    assert r.status_code == 422
    assert await _rows(sm, o.id) == []


async def test_note_never_logged(api: httpx.AsyncClient, sm: Any, fb_owners: Callable[[], Any]) -> None:
    o = await fb_owners()
    _, mid = await _turn(sm, o)
    secret = "private-note-text-7731"
    with capture_logs() as logs:
        r = await api.post(
            "/v1/feedback",
            json={"message_id": str(mid), "rating": "down", "reason": "other", "note": secret},
            headers=o.auth,
        )
    assert r.status_code == 200
    assert secret not in repr(logs)


async def test_summary_counts(sm: Any, api: httpx.AsyncClient, fb_owners: Callable[[], Any]) -> None:
    """`feedback_summary` is what `/v1/metrics/chat` embeds as `feedback`: counts per owner."""
    a, b = await fb_owners(), await fb_owners()
    _, ma = await _turn(sm, a)
    _, mb = await _turn(sm, b)
    posts = [
        (a, {"message_id": str(ma), "rating": "down", "reason": "wrong"}),
        (a, {"message_id": str(ma), "rating": "up", "segment_ordinal": 0}),
        (a, {"message_id": str(ma), "rating": "down", "reason": "wrong", "segment_ordinal": 1}),
        (b, {"message_id": str(mb), "rating": "down", "reason": "incomplete"}),
    ]
    for owner, body in posts:
        assert (await api.post("/v1/feedback", json=body, headers=owner.auth)).status_code == 200
    async with sm() as s:
        mine = await feedback_summary(s, a.id, 3600)
        theirs = await feedback_summary(s, b.id, 3600)
    assert mine == {"up": 1, "down": 2, "by_reason": {"wrong": 2}}
    assert theirs == {"up": 0, "down": 1, "by_reason": {"incomplete": 1}}
