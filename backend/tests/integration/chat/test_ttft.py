"""TTFT: turn start to the first model-written segment; stored on the run, reported as p50/p95."""

import uuid
from collections.abc import Callable
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.integration.chat.conftest import owners
from tests.support.docs import seed_document
from tests.support.ledger_env import ledger_owners
from tests.support.turn_env import (
    EMBED_SLOT,
    analysis,
    answer_script,
    install_runner,
    new_session,
    post,
    seed_chunk,
)

__all__ = ["ledger_owners", "owners"]  # fixture imports

Q = "Services twelve months"


async def _runs(sm: async_sessionmaker[AsyncSession], owner_id: Any) -> list[Any]:
    async with sm() as s:
        rows = await s.execute(
            text("SELECT ttft_ms, latency_ms FROM rag_runs WHERE owner_id = :o ORDER BY created_at"),
            {"o": owner_id},
        )
        return list(rows.all())


async def test_recorded(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    o = await ledger_owners()
    await seed_chunk(sm, o.id, "Services Agreement")
    script = answer_script(Q, ("Twelve months.", ["E1.0"]), ("Thirty days.", ["E1.1"]))
    install_runner(api, sm, script + answer_script(Q, ("Twelve months.", ["E1.0"])))
    for cmid, sse in (("json", False), ("sse", True)):  # JSON and SSE modes both record it
        sid = await new_session(api, o)
        assert (await post(api, o, sid, Q, cmid, sse=sse)).status_code == 200
    runs = await _runs(sm, o.id)
    assert len(runs) == 2
    for ttft, latency in runs:
        assert ttft is not None and 0 < ttft <= latency
    m = (await api.get("/v1/metrics/chat?window=1h", headers=o.auth)).json()
    assert m["ttft_ms"]["p50"] is not None and m["ttft_ms"]["p95"] >= m["ttft_ms"]["p50"]


async def test_abstain_null(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    """A gate abstention (nothing retrieved, no generation) stores NULL and stays out of the percentiles."""
    o = await ledger_owners()
    await seed_chunk(sm, o.id, "Services Agreement")
    empty = await seed_document(sm, o.id, title="Empty")  # no chunks
    install_runner(api, sm, [*answer_script(Q, ("Twelve months.", ["E1.0"])), analysis("zzz"), EMBED_SLOT])
    sid = await new_session(api, o)
    assert (await post(api, o, sid, Q, "a1")).json()["message"]["response_type"] == "answer"
    scoped = await api.post(
        "/v1/chat/sessions", json={"document_ids": [str(empty.document_id)]}, headers=o.auth
    )
    r = await post(api, o, uuid.UUID(scoped.json()["id"]), "zzz unrelated", "a2")
    assert r.json()["message"]["response_type"] == "abstain"
    runs = await _runs(sm, o.id)
    assert len(runs) == 2 and sorted(t is None for t, _ in runs) == [False, True]
    answered = next(t for t, _ in runs if t is not None)
    m = (await api.get("/v1/metrics/chat?window=1h", headers=o.auth)).json()
    assert m["turns"] == 2 and m["ttft_ms"]["p50"] == answered and m["ttft_ms"]["p95"] == answered
