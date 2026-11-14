"""`node_latency_ms` in GET /v1/metrics/chat equals hand-computed percentiles on a fixture."""

import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from tests.integration.metrics.test_chat_metrics import run, seed_session


@pytest.fixture
async def owners(mk_owner: Callable[..., Any], sm: Any) -> AsyncIterator[Callable[..., Any]]:
    """`mk_owner` plus removal of the fixture's `run_steps` before its own teardown deletes the runs."""
    made: list[uuid.UUID] = []

    async def mk() -> Any:
        o = await mk_owner()
        made.append(o.id)
        return o

    yield mk
    async with sm() as s:
        await s.execute(
            text("DELETE FROM run_steps WHERE run_id IN (SELECT id FROM rag_runs WHERE owner_id = ANY(:i))"),
            {"i": made},
        )
        await s.commit()


async def step(sm: Any, run_id: uuid.UUID, seq: int, node: str, ms: int | None, status: str = "ok") -> None:
    async with sm() as s:
        await s.execute(
            text(
                "INSERT INTO run_steps (id, run_id, seq, node, status, started_at, duration_ms) "
                "VALUES (gen_random_uuid(), :r, :q, :n, :st, now(), :d)"
            ),
            {"r": run_id, "q": seq, "n": node, "st": status, "d": ms},
        )
        await s.commit()


async def test_values(api: httpx.AsyncClient, sm: Any, owners: Callable[..., Any]) -> None:
    a, b = await owners(), await owners()
    sid = await seed_session(sm, a.id)
    gen = [1000, 2000, 3000, 4000]
    for i, g in enumerate(gen):
        rid = await run(sm, a.id, sid, minutes_ago=10 + i, latency=g, ttft=None, cost="0.01")
        await step(sm, rid, 1, "analyze", 100 * (i + 1) if i < 3 else 0, "ok" if i < 3 else "skipped")
        await step(sm, rid, 2, "generate", g)
    old = await run(sm, a.id, sid, minutes_ago=2 * 1440, latency=1, ttft=None, cost="0.01")
    await step(sm, old, 1, "generate", 999999)  # outside the 24h window
    sb = await seed_session(sm, b.id)
    rb = await run(sm, b.id, sb, minutes_ago=5, latency=1, ttft=None, cost="0.01")
    await step(sm, rb, 1, "generate", 777777)  # another owner

    m = (await api.get("/v1/metrics/chat", headers=a.auth)).json()
    # generate [1000, 2000, 3000, 4000]: p50 = 2500; p95 rank 0.95 * 3 = 2.85 -> 3000 + 0.85 * 1000 = 3850.
    # analyze [100, 200, 300] (skipped step not counted): p50 = 200; p95 rank 1.9 -> 200 + 0.9 * 100 = 290.
    assert m["node_latency_ms"] == {
        "analyze": {"p50": 200.0, "p95": 290.0},
        "generate": {"p50": 2500.0, "p95": 3850.0},
    }
