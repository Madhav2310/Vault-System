"""S14.01 per-user Redis token bucket (design §13.4)."""

import asyncio
import uuid
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest
import redis.asyncio as aioredis
from structlog.testing import capture_logs

from app.core.ratelimit import get_ratelimit_redis
from app.core.settings import get_settings


@pytest.fixture(autouse=True)
def slow_refill(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A one-hour window makes refill negligible, so counts are exact however slow the test machine is."""
    monkeypatch.setattr(get_settings(), "rate_limit_window_s", 3600)
    yield


async def _new_session(api: httpx.AsyncClient, owner: Any) -> str:
    r = await api.post("/v1/chat/sessions", json={}, headers=owner.auth)
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


async def _chat_post(api: httpx.AsyncClient, owner: Any, session_id: str) -> httpx.Response:
    return await api.post(
        f"/v1/chat/sessions/{session_id}/messages",
        json={"content": "hello", "client_message_id": "same"},  # same id: a dedupe, cheap and repeatable
        headers=owner.auth,
    )


async def test_chat_limit(api: httpx.AsyncClient, make_owner: Callable[[], Any]) -> None:
    o = await make_owner()
    sid = await _new_session(api, o)
    codes = [(await _chat_post(api, o, sid)).status_code for _ in range(20)]
    assert 429 not in codes
    r = await _chat_post(api, o, sid)
    assert r.status_code == 429
    assert r.headers["content-type"].startswith("application/problem+json")
    body = r.json()
    assert body["code"] == "rate_limited" and body["retryable"] is True
    assert int(r.headers["retry-after"]) >= 1
    assert r.headers["x-ratelimit-remaining"] == "0"


async def test_headers(api: httpx.AsyncClient, make_owner: Callable[[], Any]) -> None:
    o = await make_owner()
    seen = []
    for _ in range(3):
        r = await api.get("/v1/documents", headers=o.auth)
        assert r.status_code == 200
        assert r.headers["x-ratelimit-limit"] == "300"
        assert int(r.headers["x-ratelimit-reset"]) >= 1
        seen.append(int(r.headers["x-ratelimit-remaining"]))
    assert seen == [299, 298, 297]
    # the same values under the standard (draft) names
    assert r.headers["ratelimit-limit"] == "300" and r.headers["ratelimit-remaining"] == "297"
    # an upload counts against its own bucket (30/min), not the default one
    r = await api.post("/v1/documents", content=b"", headers=o.auth)
    assert r.headers["x-ratelimit-limit"] == "30" and r.headers["x-ratelimit-remaining"] == "29"


async def test_per_user(api: httpx.AsyncClient, make_owner: Callable[[], Any]) -> None:
    a, b = await make_owner(), await make_owner()
    sa, sb = await _new_session(api, a), await _new_session(api, b)
    for _ in range(20):
        await _chat_post(api, a, sa)
    assert (await _chat_post(api, a, sa)).status_code == 429
    rb = await _chat_post(api, b, sb)
    assert rb.status_code != 429 and rb.headers["x-ratelimit-remaining"] == "19"


async def test_fail_open(api: httpx.AsyncClient, make_owner: Callable[[], Any]) -> None:
    o = await make_owner()
    dead = aioredis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.2, socket_timeout=0.2)

    async def _dead() -> aioredis.Redis:
        return dead

    app = api._transport.app  # type: ignore[attr-defined]
    app.dependency_overrides[get_ratelimit_redis] = _dead
    try:
        with capture_logs() as logs:
            r = await api.get("/v1/documents", headers=o.auth)
            assert r.status_code == 200
            sid = await _new_session(api, o)
            # past the chat limit of 20, on a bucket that cannot be counted
            codes = {(await _chat_post(api, o, sid)).status_code for _ in range(22)}
        assert 429 not in codes
        assert "x-ratelimit-limit" not in r.headers
        warns = [x for x in logs if x["event"] == "ratelimit_fail_open"]
        assert warns and warns[0]["log_level"] == "warning"
    finally:
        app.dependency_overrides.pop(get_ratelimit_redis, None)
        await dead.aclose()


async def test_atomic_under_concurrency(api: httpx.AsyncClient, make_owner: Callable[[], Any]) -> None:
    o = await make_owner()
    missing = uuid.uuid4()  # a 404 handler: the limiter runs before it, and the request is cheap

    async def one() -> int:
        r = await api.post(
            f"/v1/chat/sessions/{missing}/messages",
            json={"content": "x", "client_message_id": "c"},
            headers=o.auth,
        )
        return r.status_code

    codes = await asyncio.gather(*(one() for _ in range(50)))
    assert codes.count(429) == 30
    assert codes.count(404) == 20
