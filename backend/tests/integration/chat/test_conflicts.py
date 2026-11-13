"""S18.07 conflicting sources end to end: persisted on the run, returned by POST, history and the SSE `done`,
dropped when invalid, owner-scoped, redacted when a document is deleted."""

import uuid
from collections.abc import Callable
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.chat.conflicts import CONFLICT_DETECTED, load_conflicts
from app.llm.providers.fake import structured
from tests.integration.chat.conftest import owners
from tests.support.ledger_env import ledger_owners
from tests.support.turn_env import (
    EMBED_SLOT,
    analysis,
    answer,
    install_runner,
    new_session,
    parse_sse,
    post,
    seed_chunk,
)

__all__ = ["ledger_owners", "owners"]  # fixture imports

Q = "notice period"
A_TEXT = "The alpha notice period is thirty days."
B_TEXT = "The beta notice period is ninety days."
SEG = ("The two agreements give different notice periods.", ["E1.0", "E2.0"])
VERDICT = structured({"verdicts": [{"index": 0, "verdict": "supported", "reason": "ok"}]})


def _conflict(a: list[str], b: list[str]) -> dict[str, Any]:
    return {"topic": "notice period", "handles_a": a, "handles_b": b}


async def _setup(
    sm: async_sessionmaker[AsyncSession],
    ledger_owners: Callable[[], Any],
    api: httpx.AsyncClient,
    conflicts: list[dict[str, Any]],
) -> tuple[Any, uuid.UUID]:
    o = await ledger_owners()
    await seed_chunk(sm, o.id, "Alpha Agreement", A_TEXT)
    await seed_chunk(sm, o.id, "Beta Agreement", B_TEXT)
    install_runner(api, sm, [analysis(Q), EMBED_SLOT, answer(SEG, conflicts=conflicts), VERDICT])
    return o, await new_session(api, o)


def _sides(conflict: dict[str, Any]) -> tuple[set[str], set[str]]:
    return (
        {e["cited_text"] for e in conflict["side_a"]},
        {e["cited_text"] for e in conflict["side_b"]},
    )


async def test_valid_persisted(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    o, sid = await _setup(sm, ledger_owners, api, [_conflict(["E1.0"], ["E2.0"])])
    r = await post(api, o, sid, Q, "m1")
    assert r.status_code == 200
    [c] = r.json()["message"]["conflicts"]  # the JSON-mode answer
    a, b = _sides(c)
    assert a | b == {A_TEXT, B_TEXT} and a != b and c["topic"] == "notice period"
    assert {e["document_title"] for e in (*c["side_a"], *c["side_b"])} == {
        "Alpha Agreement",
        "Beta Agreement",
    }
    hist = (await api.get(f"/v1/chat/sessions/{sid}/messages", headers=o.auth)).json()["items"]
    assert hist[0]["conflicts"] == [] and hist[1]["conflicts"] == [
        c
    ]  # history exposes the same, user message has none
    async with sm() as s:
        row = (
            await s.execute(
                text("SELECT id, conflicts, reason_codes FROM rag_runs WHERE owner_id = :o"), {"o": o.id}
            )
        ).one()
        assert len(row.conflicts["items"]) == 1 and CONFLICT_DETECTED in row.reason_codes


async def test_sse_done_carries_conflicts(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    o, sid = await _setup(sm, ledger_owners, api, [_conflict(["E1.0"], ["E2.0"])])
    events = parse_sse((await post(api, o, sid, Q, "m1", sse=True)).text)
    done = events[-1]
    assert done["type"] == "done" and len(done["conflicts"]) == 1
    a, b = _sides(done["conflicts"][0])
    assert a | b == {A_TEXT, B_TEXT}


async def test_invalid_dropped_and_none_by_default(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    o, sid = await _setup(
        sm, ledger_owners, api, [_conflict(["E1.0"], ["E1.0"]), _conflict(["E1.0"], ["E9.9"])]
    )
    r = await post(api, o, sid, Q, "m1")
    assert r.json()["message"]["conflicts"] == []
    async with sm() as s:
        row = (
            await s.execute(
                text("SELECT conflicts, reason_codes FROM rag_runs WHERE owner_id = :o"), {"o": o.id}
            )
        ).one()
    assert row.conflicts is None and CONFLICT_DETECTED not in row.reason_codes


async def test_owner_scoped_and_redacted(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    o, sid = await _setup(sm, ledger_owners, api, [_conflict(["E1.0"], ["E2.0"])])
    await post(api, o, sid, Q, "m1")
    other = await ledger_owners()
    async with sm() as s:
        run_id = (
            await s.execute(text("SELECT id FROM rag_runs WHERE owner_id = :o"), {"o": o.id})
        ).scalar_one()
        assert await load_conflicts(s, other.id, [run_id]) == {}  # another owner reads nothing
        assert len((await load_conflicts(s, o.id, [run_id]))[run_id]) == 1
        await s.execute(
            text("UPDATE documents SET deleted_at = now() WHERE owner_id = :o AND title = 'Beta Agreement'"),
            {"o": o.id},
        )
        await s.commit()
    async with sm() as s:  # one side's document is deleted: the conflict is no longer shown
        assert await load_conflicts(s, o.id, [run_id]) == {}
