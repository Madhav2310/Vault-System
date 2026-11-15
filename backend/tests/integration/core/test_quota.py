"""Daily cost quota per user: chat 429, admin exemption, brief deferral."""

import asyncio
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
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


async def _in_flight(owner_id: uuid.UUID) -> int:
    from app.core.quota import KEY_PREFIX
    from app.core.ratelimit import get_ratelimit_redis

    return int(await (await get_ratelimit_redis()).zcard(f"{KEY_PREFIX}:{owner_id}"))


async def test_concurrent_turns_are_held_to_the_quota(
    api: httpx.AsyncClient, sm: Any, make_owner: Callable[[], Any]
) -> None:
    """$0.90 of $1.00 spent, five concurrent turns of $0.04 each: a turn is admitted only while spend plus
    the reservations of the turns in flight ($0.05 each) stays under the quota, so two start, spend ends at
    $0.98, and every reservation is released when its turn ends."""
    from app.core.quota import TURN_RESERVE_USD

    assert Decimal("0.04") <= TURN_RESERVE_USD
    o = await make_owner()
    await _spend(sm, o.id, "0.90")

    async def costly(*_a: object) -> None:
        await asyncio.sleep(0.2)  # all five requests are admitted or refused while these turns run
        await _spend(sm, o.id, "0.04")

    api._transport.app.dependency_overrides[get_turn_runner] = lambda: costly  # type: ignore[attr-defined]
    rs = await asyncio.gather(*(_post(api, o) for _ in range(5)))
    assert sorted(r.status_code for r in rs) == [202, 202, 429, 429, 429], [r.text for r in rs]
    async with sm() as s:
        spent = (
            await s.execute(text("SELECT sum(cost_usd) FROM llm_calls WHERE owner_id = :o"), {"o": o.id})
        ).scalar_one()
    assert spent == Decimal("0.98")
    assert await _in_flight(o.id) == 0
    assert (await _post(api, o)).status_code == 202  # nothing held any more: $0.98 is still under $1.00


async def test_a_request_that_starts_no_turn_releases_its_reservation(
    api: httpx.AsyncClient, make_owner: Callable[[], Any]
) -> None:
    o = await make_owner()
    missing = uuid.uuid4()
    r = await api.post(
        f"/v1/chat/sessions/{missing}/messages",
        json={"content": "hello", "client_message_id": "m1"},
        headers=o.auth,
    )
    assert r.status_code == 404
    assert await _in_flight(o.id) == 0


async def test_an_invalid_request_holds_no_reservation(
    api: httpx.AsyncClient, make_owner: Callable[[], Any]
) -> None:
    """Admission runs after validation: a 422 (bad body or path) never reserves quota."""
    o = await make_owner()
    r = await api.post("/v1/chat/sessions", json={}, headers=o.auth)
    sid = r.json()["id"]
    bad_body = await api.post(
        f"/v1/chat/sessions/{sid}/messages", json={"content": "x" * 100_000}, headers=o.auth
    )
    bad_path = await api.post(
        "/v1/chat/sessions/not-a-uuid/messages",
        json={"content": "hi", "client_message_id": "m"},
        headers=o.auth,
    )
    assert bad_body.status_code == bad_path.status_code == 422
    assert await _in_flight(o.id) == 0
