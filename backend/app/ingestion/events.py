"""Live processing status (`/processing/stream`).

`job_events` is the only source of truth. Redis pub/sub carries no data, just a wake-up ("something changed
for version V") published by the runner after each stage commit. The stream subscribes first, then reads
`job_events` past its cursor, so nothing between catch-up and live can be missed, and a lost or duplicated
wake-up is harmless. Without pub/sub (Redis down, subscription lost) the stream polls the table every
`poll_s`; with it, a slow poll (`idle_s`) still backstops a lost message.

No connection is held while waiting: every read is its own short session.
"""

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

import redis.asyncio as aioredis
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import get_logger

HEARTBEAT_S = 15.0
MAX_LIFETIME_S = 600.0
POLL_S = 1.0  # fallback polling period when pub/sub is unavailable
IDLE_S = 5.0  # backstop poll while pub/sub is healthy
PUBLISH_TIMEOUT_S = 0.25
# A version status that ends the stream whatever the job says; otherwise the job's own end (succeeded/dead).
DONE_VERSION_STATUSES = frozenset({"enriched", "needs_ocr", "unsupported", "failed", "superseded"})
DONE_JOB_STATUSES = frozenset({"succeeded", "dead"})

_log = get_logger("vault.ingestion.events")


def channel(version_id: uuid.UUID) -> str:
    return f"vault:processing:{version_id}"


async def publish_wake(redis_url: str, version_id: uuid.UUID) -> None:
    """Best effort: a failure here must never affect the job, so nothing escapes."""
    client = aioredis.from_url(
        redis_url, socket_connect_timeout=PUBLISH_TIMEOUT_S, socket_timeout=PUBLISH_TIMEOUT_S
    )
    try:
        await client.publish(channel(version_id), "1")
    except Exception as e:
        _log.warning("processing_publish_failed", version_id=str(version_id), error_type=type(e).__name__)
    finally:
        try:
            await client.aclose()
        except Exception:  # closing a client that never connected
            _log.debug("processing_publish_close_failed")


def sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


HEARTBEAT_FRAME = ": heartbeat\n\n"


async def _read(
    sm: async_sessionmaker[AsyncSession],
    owner_id: uuid.UUID,
    version_id: uuid.UUID,
    cursor: tuple[Any, uuid.UUID] | None,
) -> tuple[dict[str, Any] | None, list[Any]]:
    """(state, new events). State first, events second: an event is committed together with the state change
    that follows it, so a terminal state read here implies the job's events are visible to the next read."""
    async with sm() as s:
        state = (
            (
                await s.execute(
                    text(
                        "SELECT v.status AS vstatus, v.error_code AS verror, j.id AS job_id, "
                        "j.status AS jstatus, j.error_code AS jerror FROM document_versions v "
                        "LEFT JOIN processing_jobs j ON j.version_id = v.id AND j.owner_id = v.owner_id "
                        "WHERE v.id = :v AND v.owner_id = :o"
                    ),
                    {"v": version_id, "o": owner_id},
                )
            )
            .mappings()
            .first()
        )
        if state is None or state["job_id"] is None:
            return (dict(state) if state else None), []
        params: dict[str, Any] = {"j": state["job_id"]}
        after = ""
        if cursor is not None:
            after = "AND (created_at, id) > (:ca, :ci) "
            params.update(ca=cursor[0], ci=cursor[1])
        rows = (
            (
                await s.execute(
                    text(
                        "SELECT id, stage, status, attempt, detail, created_at FROM job_events "  # noqa: S608
                        f"WHERE job_id = :j {after}ORDER BY created_at, id"
                    ),
                    params,
                )
            )
            .mappings()
            .all()
        )
        return dict(state), list(rows)


def _stage_frame(r: Any) -> str:
    data: dict[str, Any] = {
        "name": r["stage"],
        "status": r["status"],
        "attempt": r["attempt"],
        "at": r["created_at"].isoformat(),
    }
    code = (r["detail"] or {}).get("error_code") if isinstance(r["detail"], dict) else None
    if code:
        data["error_code"] = code
    return sse("stage", data)


def _is_done(state: dict[str, Any]) -> bool:
    return (
        state["job_id"] is None
        or state["jstatus"] in DONE_JOB_STATUSES
        or state["vstatus"] in DONE_VERSION_STATUSES
    )


async def stream_processing(
    sm: async_sessionmaker[AsyncSession],
    redis: aioredis.Redis | None,
    owner_id: uuid.UUID,
    version_id: uuid.UUID,
    *,
    heartbeat_s: float = HEARTBEAT_S,
    max_s: float = MAX_LIFETIME_S,
    poll_s: float = POLL_S,
    idle_s: float = IDLE_S,
) -> AsyncIterator[str]:
    """SSE frames: `stage` per job_events row (oldest first), then `done{status, error_code}`; `: heartbeat`
    comments while idle; `timeout` if the lifetime cap is reached first."""
    pubsub: Any = None
    if redis is not None:
        try:
            pubsub = redis.pubsub()
            await pubsub.subscribe(channel(version_id))
        except (RedisError, OSError, TimeoutError) as e:
            _log.warning("processing_pubsub_unavailable", error_type=type(e).__name__)
            pubsub = None
    started = last_sent = time.monotonic()
    cursor: tuple[Any, uuid.UUID] | None = None
    try:
        while True:
            state, rows = await _read(sm, owner_id, version_id, cursor)
            if state is None:  # purged while we were streaming
                yield sse("done", {"status": "deleted", "error_code": None})
                return
            for r in rows:
                yield _stage_frame(r)
                cursor = (r["created_at"], r["id"])
                last_sent = time.monotonic()
            if _is_done(state):
                yield sse(
                    "done", {"status": state["vstatus"], "error_code": state["verror"] or state["jerror"]}
                )
                return
            now = time.monotonic()
            if now - started >= max_s:
                yield sse("timeout", {"reason": "max_lifetime"})
                return
            if now - last_sent >= heartbeat_s:
                yield HEARTBEAT_FRAME
                last_sent = now
            wait = max(
                0.0,
                min(idle_s if pubsub else poll_s, heartbeat_s - (now - last_sent), max_s - (now - started)),
            )
            if pubsub is not None:
                try:
                    await pubsub.get_message(ignore_subscribe_messages=True, timeout=wait)
                except (RedisError, OSError, TimeoutError) as e:
                    _log.warning("processing_pubsub_lost", error_type=type(e).__name__)
                    pubsub = None
            else:
                await asyncio.sleep(wait)
    finally:
        if pubsub is not None:
            try:
                await pubsub.aclose()
            except Exception:  # the connection may already be gone
                _log.debug("processing_pubsub_close_failed")
