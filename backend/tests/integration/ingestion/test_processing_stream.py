"""S14.05 GET /v1/versions/{vid}/processing/stream: catch-up from job_events, live via Redis pub/sub."""

import asyncio
import datetime as dt
import json
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any

import httpx
import pytest
import redis.asyncio as aioredis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.versions import get_events_redis, get_stream_sessionmaker
from app.core.settings import get_settings
from app.ingestion.events import channel, publish_wake, stream_processing
from app.ingestion.runner import run_job
from tests.support.jobs import JobRef, make_job

T0 = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)


@pytest.fixture(autouse=True)
def stream_deps(api: httpx.AsyncClient, sm: async_sessionmaker[AsyncSession]) -> Iterator[None]:
    """The stream opens its own short sessions: point them at the test database."""
    app = api._transport.app  # type: ignore[attr-defined]
    app.dependency_overrides[get_stream_sessionmaker] = lambda: sm
    yield
    app.dependency_overrides.pop(get_stream_sessionmaker, None)
    app.dependency_overrides.pop(get_events_redis, None)


async def _event(sm: Any, ref: JobRef, stage: str, i: int, status: str = "succeeded", **detail: Any) -> None:
    async with sm() as s:
        await s.execute(
            text(
                "INSERT INTO job_events (id, job_id, stage, status, attempt, duration_ms, detail, "
                "created_at) "
                "VALUES (:id, :j, :st, :s, 1, 5, CAST(:d AS jsonb), :ca)"
            ),
            {
                "id": uuid.uuid4(),
                "j": ref.job_id,
                "st": stage,
                "s": status,
                "d": json.dumps(detail),
                "ca": T0 + dt.timedelta(seconds=i),
            },
        )
        await s.commit()


async def _finish(
    sm: Any, ref: JobRef, version_status: str, job_status: str, error: str | None = None
) -> None:
    async with sm() as s:
        await s.execute(
            text("UPDATE document_versions SET status = :s, error_code = :e WHERE id = :v"),
            {"s": version_status, "e": error, "v": ref.version_id},
        )
        await s.execute(
            text("UPDATE processing_jobs SET status = :s, error_code = :e WHERE id = :j"),
            {"s": job_status, "e": error, "j": ref.job_id},
        )
        await s.commit()


def parse(frame: str) -> tuple[str, Any]:
    ev, data = "message", None
    for line in frame.strip().splitlines():
        if line.startswith("event:"):
            ev = line[6:].strip()
        elif line.startswith("data:"):
            data = json.loads(line[5:])
        elif line.startswith(":"):
            ev = "comment"
    return ev, data


def parse_all(body: str) -> list[tuple[str, Any]]:
    return [parse(f) for f in body.split("\n\n") if f.strip()]


async def _next(gen: AsyncIterator[str], wait_s: float = 3.0) -> tuple[str, Any]:
    return parse(await asyncio.wait_for(anext(gen), wait_s))


def _gen(sm: Any, redis: Any, owner_id: Any, ref: JobRef, **kw: Any) -> AsyncIterator[str]:
    opts = {"heartbeat_s": 15.0, "max_s": 60.0, "poll_s": 1.0, "idle_s": 30.0, **kw}
    return stream_processing(sm, redis, owner_id, ref.version_id, **opts)


async def test_catchup_then_live(make_owner: Callable[[], Any], sm: Any) -> None:
    o = await make_owner()
    ref = await make_job(sm, o.id, stage="chunk", status="running")
    await _event(sm, ref, "extract", 1)
    await _event(sm, ref, "quality", 2)
    redis = aioredis.from_url(get_settings().redis_url)
    try:
        # idle_s is long: anything after the catch-up can only arrive through the pub/sub wake-up
        gen = _gen(sm, redis, o.id, ref)
        first = [await _next(gen), await _next(gen)]
        assert [(e, d["name"], d["status"]) for e, d in first] == [
            ("stage", "extract", "succeeded"),
            ("stage", "quality", "succeeded"),
        ]
        assert "at" in first[0][1]

        nxt = asyncio.ensure_future(_next(gen))
        await asyncio.sleep(0.2)  # the stream is now waiting on pub/sub
        assert not nxt.done()
        await _event(sm, ref, "chunk", 3)
        await publish_wake(get_settings().redis_url, ref.version_id)
        assert await nxt == ("stage", {**(await _stage_payload(sm, ref, "chunk"))})

        await _event(sm, ref, "index", 4)
        await _finish(sm, ref, "ready", "succeeded")
        await publish_wake(get_settings().redis_url, ref.version_id)
        assert (await _next(gen))[1]["name"] == "index"
        assert await _next(gen) == ("done", {"status": "ready", "error_code": None})
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(anext(gen), 3)
    finally:
        await redis.aclose()


async def _stage_payload(sm: Any, ref: JobRef, stage: str) -> dict[str, Any]:
    async with sm() as s:
        at = (
            await s.execute(
                text("SELECT created_at FROM job_events WHERE job_id = :j AND stage = :s"),
                {"j": ref.job_id, "s": stage},
            )
        ).scalar_one()
    return {"name": stage, "status": "succeeded", "attempt": 1, "at": at.isoformat()}


async def test_already_done(api: httpx.AsyncClient, make_owner: Callable[[], Any], sm: Any) -> None:
    o = await make_owner()
    ref = await make_job(sm, o.id, stage="brief", status="succeeded")
    for i, st in enumerate(["extract", "quality", "chunk", "embed", "index", "brief"]):
        await _event(sm, ref, st, i)
    await _finish(sm, ref, "enriched", "succeeded")
    r = await api.get(f"/v1/versions/{ref.version_id}/processing/stream", headers=o.auth)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    assert r.headers["cache-control"] == "no-cache"
    frames = parse_all(r.text)
    assert [d["name"] for e, d in frames if e == "stage"] == [
        "extract",
        "quality",
        "chunk",
        "embed",
        "index",
        "brief",
    ]
    assert frames[-1] == ("done", {"status": "enriched", "error_code": None})


async def test_cross_owner_404(api: httpx.AsyncClient, make_owner: Callable[[], Any], sm: Any) -> None:
    a, b = await make_owner(), await make_owner()
    ref = await make_job(sm, a.id)
    r = await api.get(f"/v1/versions/{ref.version_id}/processing/stream", headers=b.auth)
    assert r.status_code == 404 and r.headers["content-type"].startswith("application/problem+json")
    r = await api.get(f"/v1/versions/{uuid.uuid4()}/processing/stream", headers=a.auth)
    assert r.status_code == 404


async def test_failed_terminal(api: httpx.AsyncClient, make_owner: Callable[[], Any], sm: Any) -> None:
    o = await make_owner()
    ref = await make_job(sm, o.id, stage="extract", status="dead")
    await _event(sm, ref, "extract", 1, status="error", error_code="parse_failed", permanent=True)
    await _finish(sm, ref, "failed", "dead", "parse_failed")
    r = await api.get(f"/v1/versions/{ref.version_id}/processing/stream", headers=o.auth)
    frames = parse_all(r.text)
    assert frames[0][0] == "stage" and frames[0][1]["status"] == "error"
    assert frames[0][1]["error_code"] == "parse_failed"
    assert frames[-1] == ("done", {"status": "failed", "error_code": "parse_failed"})


async def test_pubsub_fallback(api: httpx.AsyncClient, make_owner: Callable[[], Any], sm: Any) -> None:
    o = await make_owner()
    ref = await make_job(sm, o.id, stage="chunk", status="running")
    await _event(sm, ref, "extract", 1)
    dead = aioredis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.2)

    async def _dead() -> aioredis.Redis:
        return dead

    api._transport.app.dependency_overrides[get_events_redis] = _dead  # type: ignore[attr-defined]

    async def later() -> None:  # no publish: only polling can notice
        await asyncio.sleep(0.5)
        await _event(sm, ref, "chunk", 2)
        await _finish(sm, ref, "ready", "succeeded")

    task = asyncio.ensure_future(later())
    try:
        r = await asyncio.wait_for(
            api.get(f"/v1/versions/{ref.version_id}/processing/stream", headers=o.auth), 10
        )
    finally:
        await task
        await dead.aclose()
    frames = parse_all(r.text)
    assert [d["name"] for e, d in frames if e == "stage"] == ["extract", "chunk"]
    assert frames[-1][0] == "done" and frames[-1][1]["status"] == "ready"


async def test_heartbeat_and_max_lifetime(make_owner: Callable[[], Any], sm: Any) -> None:
    o = await make_owner()
    ref = await make_job(sm, o.id, stage="chunk", status="running")
    redis = aioredis.from_url(get_settings().redis_url)
    try:
        gen = _gen(sm, redis, o.id, ref, heartbeat_s=0.2, max_s=0.7, poll_s=0.1, idle_s=0.1)
        frames = [parse(f) async for f in gen]
    finally:
        await redis.aclose()
    kinds = [e for e, _ in frames]
    assert kinds.count("comment") >= 2 and kinds[-1] == "timeout"


async def test_runner_publishes_after_stage_commit(
    make_owner: Callable[[], Any], sm: Any, enqueued: Any
) -> None:
    """The runner wakes subscribers after each stage commit (the event row is already visible then)."""
    from app.ingestion.runner import StageResult

    class Noop:
        name = "extract"

        async def run(self, ctx: Any) -> StageResult:
            return StageResult()

    o = await make_owner()
    ref = await make_job(sm, o.id, stage="extract")
    redis = aioredis.from_url(get_settings().redis_url)
    ps = redis.pubsub()
    await ps.subscribe(channel(ref.version_id))
    try:
        out = await run_job(sm, ref.job_id, "w", {"extract": Noop()}, enqueued, order=("extract", "quality"))
        assert out == "advanced"
        msg = None
        for _ in range(20):
            msg = await ps.get_message(ignore_subscribe_messages=True, timeout=0.2)
            if msg:
                break
        assert msg is not None and msg["type"] == "message"
    finally:
        await ps.aclose()
        await redis.aclose()


async def test_publish_failure_never_raises() -> None:
    await publish_wake("redis://127.0.0.1:1/0", uuid.uuid4())
