"""Daily cost quota per user: chat 429, admin exemption, brief deferral."""

import asyncio
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from app.api.deps import get_turn_runner
from tests.support.brief_kit import brief_gateway
from tests.support.brief_seed import SERVICES, ChunkSpec, insight_rows, run_brief, seed_ready_version
from tests.support.jobs import version_status


async def _spend(sm: Any, owner_id: uuid.UUID, usd: str) -> None:
    async with sm() as s:
        await s.execute(
            text(
                "INSERT INTO llm_calls (id, owner_id, purpose, lane, provider, model, attempt, cost_usd, "
                "status) VALUES (gen_random_uuid(), :o, 'chat_generate', 'interactive', 'openai', 'm', 1, "
                ":c, 'ok')"
            ),
            {"o": owner_id, "c": Decimal(usd)},
        )
        await s.commit()


async def _post(api: httpx.AsyncClient, owner: Any) -> httpx.Response:
    r = await api.post("/v1/chat/sessions", json={}, headers=owner.auth)
    assert r.status_code == 201, r.text
    return await api.post(
        f"/v1/chat/sessions/{r.json()['id']}/messages",
        json={"content": "hello", "client_message_id": "m1"},
        headers=owner.auth,
    )


async def test_chat_blocked(api: httpx.AsyncClient, sm: Any, make_owner: Callable[[], Any]) -> None:
    o = await make_owner()
    await _spend(sm, o.id, "1.50")
    r = await _post(api, o)
    assert r.status_code == 429
    body = r.json()
    assert body["code"] == "quota_exceeded" and body["retryable"] is True
    now = datetime.now(UTC)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    assert abs(int(r.headers["retry-after"]) - (midnight - now).total_seconds()) <= 5


async def test_under_quota_and_admin(api: httpx.AsyncClient, sm: Any, make_owner: Callable[[], Any]) -> None:
    under = await make_owner()
    await _spend(sm, under.id, "0.40")
    r = await _post(api, under)
    assert r.status_code == 202, r.text  # the integration turn runner is a no-op: the turn was accepted

    admin = await make_owner()
    async with sm() as s:
        await s.execute(text("UPDATE users SET is_admin = true WHERE id = :i"), {"i": admin.id})
        await s.commit()
    await _spend(sm, admin.id, "50")
    assert (await _post(api, admin)).status_code == 202


async def test_brief_deferred(sm: Any, make_owner: Callable[[], Any], enqueued: Any) -> None:
    o = await make_owner()
    seeded = await seed_ready_version(sm, o.id, [ChunkSpec(SERVICES)])
    await _spend(sm, o.id, "2.00")
    gw, prov, ledger = brief_gateway([])
    assert await run_brief(sm, seeded.ref.job_id, gw, enqueued) == "finished"
    assert prov.requests == [] and ledger.records == []  # no model call was made
    rows = await insight_rows(sm, seeded.ref.version_id)
    assert [r["status"] for r in rows] == ["quota_deferred"]
    assert (await version_status(sm, seeded.ref.version_id))[0] == "ready"


@pytest.mark.xfail(
    strict=True,
    reason="admission-only quota: spend is read through a 30 s cache and nothing is reserved per turn",
)
async def test_turns_cannot_spend_past_the_quota(
    api: httpx.AsyncClient, sm: Any, make_owner: Callable[[], Any]
) -> None:
    """Spend $0.90 of $1.00, then turns that each cost $0.50: at most one turn may start (the one that
    crosses the limit), whether the next ones arrive concurrently or right after it."""
    o = await make_owner()
    await _spend(sm, o.id, "0.90")

    async def costly(*_a: object) -> None:
        await asyncio.sleep(0.1)
        await _spend(sm, o.id, "0.50")

    api._transport.app.dependency_overrides[get_turn_runner] = lambda: costly  # type: ignore[attr-defined]
    concurrent = await asyncio.gather(*(_post(api, o) for _ in range(4)))
    after = await _post(api, o)
    started = sum(1 for r in [*concurrent, after] if r.status_code in (200, 202))
    async with sm() as s:
        spent = (
            await s.execute(text("SELECT sum(cost_usd) FROM llm_calls WHERE owner_id = :o"), {"o": o.id})
        ).scalar_one()
    assert started <= 1, f"{started} turns started, ${spent} spent against a $1.00 quota"
