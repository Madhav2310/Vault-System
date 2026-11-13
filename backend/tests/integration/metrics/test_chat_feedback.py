"""S18.10 AC1: `/v1/metrics/chat` carries `feedback {up, down, by_reason}` matching a seeded fixture."""

import uuid
from collections.abc import Callable
from typing import Any

import httpx
from sqlalchemy import text

from app.core.ids import uuid7
from tests.integration.metrics.test_chat_metrics import run, seed_session


async def _fb(sm: Any, owner_id: uuid.UUID, run_id: uuid.UUID, kind: str, *, minutes_ago: float = 1) -> None:
    async with sm() as s:
        await s.execute(
            text(
                "INSERT INTO feedback (id, owner_id, run_id, kind, created_at) "
                "VALUES (:i, :o, :r, :k, now() - make_interval(secs => CAST(:m AS float8) * 60))"
            ),
            {"i": uuid7(), "o": owner_id, "r": run_id, "k": kind, "m": minutes_ago},
        )
        await s.commit()


async def test_values(api: httpx.AsyncClient, sm: Any, mk_owner: Callable[..., Any]) -> None:
    a, b = await mk_owner(), await mk_owner()
    sa, sb = await seed_session(sm, a.id), await seed_session(sm, b.id)
    runs_a = [await run(sm, a.id, sa, minutes_ago=5, latency=10, ttft=5, cost="0.01") for _ in range(4)]
    run_b = await run(sm, b.id, sb, minutes_ago=5, latency=10, ttft=5, cost="0.01")
    try:
        await _fb(sm, a.id, runs_a[0], "up")
        await _fb(sm, a.id, runs_a[1], "down:wrong")
        await _fb(sm, a.id, runs_a[2], "down:wrong")
        await _fb(sm, a.id, runs_a[3], "down")  # no reason: counted as down only
        await _fb(sm, a.id, runs_a[3], "down:other", minutes_ago=3 * 60)  # outside the 1h window
        await _fb(sm, b.id, run_b, "down:incomplete")
        r = await api.get("/v1/metrics/chat", params={"window": "1h"}, headers=a.auth)
        assert r.status_code == 200, r.text
        assert r.json()["feedback"] == {"up": 1, "down": 3, "by_reason": {"wrong": 2}}
        r = await api.get("/v1/metrics/chat", params={"window": "24h"}, headers=a.auth)
        assert r.json()["feedback"] == {"up": 1, "down": 4, "by_reason": {"other": 1, "wrong": 2}}
        r = await api.get("/v1/metrics/chat", params={"window": "1h"}, headers=b.auth)
        assert r.json()["feedback"] == {"up": 0, "down": 1, "by_reason": {"incomplete": 1}}
    finally:
        async with sm() as s:
            await s.execute(text("DELETE FROM feedback WHERE owner_id = ANY(:ids)"), {"ids": [a.id, b.id]})
            await s.commit()
