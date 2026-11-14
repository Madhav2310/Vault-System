"""Stage runner.

One Celery delivery runs one stage of one job:
1. claim the job (lease); no lease → another worker has it → no-op
2. run the stage's compute with **no DB connection held**, heartbeating the lease meanwhile
3. commit in one transaction, fenced by our lease: replace the stage's outputs, advance `current_stage`,
   append a `job_events` row; then dispatch the next stage
4. on error: permanent → terminal now; retryable → backoff (`next_attempt_at`) up to `max_attempts`,
   then `dead`

Stages are idempotent: their `write` deletes the stage's previous outputs for the version before inserting.
CPU-heavy stage code must run in a thread (`asyncio.to_thread`) so the heartbeat keeps the lease alive.
"""

import asyncio
import contextlib
import pathlib
import random
import time
import traceback
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import get_logger
from app.core.settings import get_settings
from app.ingestion import status as vs
from app.ingestion.errors import PermanentStageError, RetryableStageError, StageError
from app.ingestion.events import publish_wake
from app.ingestion.jobs import HEARTBEAT_S, LEASE_S, ClaimedJob, Enqueue, claim, dispatch, heartbeat

STAGE_ORDER: tuple[str, ...] = ("extract", "quality", "chunk", "embed", "index", "brief", "contextualize")
BACKOFF_BASE_S, BACKOFF_CAP_S, JITTER = 10.0, 600.0, 0.2
_log = get_logger("vault.ingestion.runner")

Outcome = Literal["noop", "advanced", "finished", "retry", "dead", "lost_lease", "deleted"]


@dataclass(frozen=True)
class StageContext:
    job: ClaimedJob
    sm: async_sessionmaker[AsyncSession]  # short read sessions only; never held across slow work
    services: Mapping[str, Any] = field(default_factory=dict)  # object store, gateway, settings, ...


@dataclass(frozen=True)
class StageResult:
    write: Callable[[AsyncSession], Awaitable[None]] | None = None  # runs inside the fenced commit
    terminal_status: str | None = None  # ends the pipeline successfully with this version status
    detail: dict[str, Any] | None = None  # recorded on the job_events row (IDs and counts only)


class Stage(Protocol):
    name: str

    async def run(self, ctx: StageContext) -> StageResult: ...


def backoff_s(attempt: int, rng: Callable[[], float] = random.random) -> float:
    base: float = min(BACKOFF_BASE_S * 2**attempt, BACKOFF_CAP_S)
    return base * (1 - JITTER + 2 * JITTER * float(rng()))


async def _fence(s: AsyncSession, job: ClaimedJob, worker: str) -> bool:
    """Lock our job row if (and only if) we still hold its lease."""
    row = (
        await s.execute(
            text(
                "SELECT id FROM processing_jobs WHERE id = :id AND lease_owner = :w AND status = 'running' "
                "FOR UPDATE"
            ),
            {"id": job.id, "w": worker},
        )
    ).first()
    return row is not None


async def _document_deleted(s: AsyncSession, version_id: uuid.UUID, owner_id: uuid.UUID) -> bool:
    row = (
        await s.execute(
            text(
                "SELECT 1 FROM document_versions v JOIN documents d ON d.id = v.document_id "
                "WHERE v.id = :v AND v.owner_id = :o AND d.deleted_at IS NOT NULL"
            ),
            {"v": version_id, "o": owner_id},
        )
    ).first()
    return row is not None


async def _event(
    s: AsyncSession, job: ClaimedJob, stage: str, status: str, attempt: int, ms: int, detail: Any
) -> None:
    import json

    await s.execute(
        text(
            "INSERT INTO job_events (id, job_id, stage, status, attempt, duration_ms, detail) "
            "VALUES (:id, :j, :st, :status, :a, :ms, CAST(:d AS jsonb))"
        ),
        {
            "id": uuid.uuid4(),
            "j": job.id,
            "st": stage,
            "status": status,
            "a": attempt,
            "ms": ms,
            "d": json.dumps(detail),
        },
    )


async def _heartbeat_loop(
    sm: async_sessionmaker[AsyncSession],
    job: ClaimedJob,
    worker: str,
    every_s: float,
    lease_s: int,
    on_lost: Callable[[], None],
) -> None:
    """Extend the lease until cancelled. Never raises: a transient DB error skips one beat (the lease has
    minutes of slack), and a lease that is genuinely no longer ours cancels the stage so it stops spending."""
    while True:
        await asyncio.sleep(every_s)
        try:
            async with sm() as s:
                ok = await heartbeat(s, job.id, worker, lease_s)
        except Exception as e:
            _log.warning("heartbeat_error", job_id=str(job.id), error_type=type(e).__name__)
            continue
        if not ok:
            _log.warning("lease_lost", job_id=str(job.id), stage=job.current_stage)
            on_lost()
            return


def _where(e: BaseException) -> str:
    """Where an exception was raised, without its message: SQL errors embed parameter values, and those can
    be document text, which never goes into logs (CLAUDE.md)."""
    tb = traceback.extract_tb(e.__traceback__)
    return f"{pathlib.PurePath(tb[-1].filename).name}:{tb[-1].lineno}" if tb else "?"


async def run_job(
    sm: async_sessionmaker[AsyncSession],
    job_id: uuid.UUID,
    worker: str,
    stages: Mapping[str, Stage],
    enqueue: Enqueue,
    *,
    order: Sequence[str] = STAGE_ORDER,
    services: Mapping[str, Any] | None = None,
    lease_s: int = LEASE_S,
    heartbeat_s: float = HEARTBEAT_S,
    rng: Callable[[], float] = random.random,
) -> Outcome:
    async with sm() as s:
        job = await claim(s, job_id, worker, lease_s)
    if job is None:
        return "noop"
    async with sm() as s:
        current = (
            await s.execute(text("SELECT status FROM document_versions WHERE id = :v"), {"v": job.version_id})
        ).scalar_one()
        if current in (vs.UPLOADED, vs.FAILED):
            await vs.set_version_status(s, job.version_id, vs.PROCESSING)
            await s.commit()
    if job.attempt_count >= job.max_attempts:
        # `claim` counts a takeover of an expired lease as an attempt: a stage that keeps killing its worker
        # (out of memory, crash) must end in `dead`, not loop forever behind the sweeper.
        lost_worker = PermanentStageError(
            "worker_lost", "the worker died repeatedly while running this stage"
        )
        return await _fail(sm, job, worker, lost_worker, 0, rng)

    stage_name = job.current_stage
    t0 = time.monotonic()
    err: StageError | None = None
    result: StageResult | None = None
    stage = stages.get(stage_name)
    lost = False

    async def run_stage() -> StageResult:
        if stage is None:
            raise RetryableStageError("stage_unavailable", f"no implementation for stage {stage_name}")
        return await stage.run(StageContext(job, sm, services or {}))

    stage_task = asyncio.ensure_future(run_stage())

    def on_lost() -> None:
        nonlocal lost
        lost = True
        stage_task.cancel()

    hb = asyncio.create_task(_heartbeat_loop(sm, job, worker, heartbeat_s, lease_s, on_lost))
    try:
        result = await stage_task
    except asyncio.CancelledError:
        if not lost:
            raise  # the delivery itself was cancelled (shutdown/revoke): let it propagate
    except StageError as e:
        err = e
    except Exception as e:  # unexpected bug or dependency failure: retry, then dead
        _log.error(
            "stage_crashed",
            job_id=str(job.id),
            stage=stage_name,
            error_type=type(e).__name__,
            where=_where(e),
        )
        err = RetryableStageError("internal", type(e).__name__)
    finally:
        hb.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await hb
    if lost:
        return "lost_lease"
    ms = int((time.monotonic() - t0) * 1000)
    if err is not None:
        return await _fail(sm, job, worker, err, ms, rng)
    assert result is not None
    try:
        return await _commit(sm, job, worker, result, ms, order, enqueue)
    except Exception as e:
        # A failing `write` (bad data, an illegal transition, a deadlock) must be counted like any stage
        # failure; otherwise the job stays `running`, the sweeper redelivers it and it fails the same way
        # forever. The transaction was rolled back when `_commit` unwound.
        _log.error(
            "commit_failed",
            job_id=str(job.id),
            stage=stage_name,
            error_type=type(e).__name__,
            where=_where(e),
        )
        failure = RetryableStageError("commit_failed", type(e).__name__)
        return await _fail(sm, job, worker, failure, ms, rng)


async def _commit(
    sm: async_sessionmaker[AsyncSession],
    job: ClaimedJob,
    worker: str,
    result: StageResult,
    ms: int,
    order: Sequence[str],
    enqueue: Enqueue,
) -> Outcome:
    stage = job.current_stage
    idx = order.index(stage)
    # `ready` is the one terminal status the pipeline continues past: enrichment stages (brief) follow it.
    ends = result.terminal_status is not None and result.terminal_status != vs.READY
    nxt = None if ends or idx + 1 >= len(order) else order[idx + 1]
    async with sm() as s:
        if not await _fence(s, job, worker):
            await s.rollback()
            return "lost_lease"
        if await _document_deleted(
            s, job.version_id, job.owner_id
        ):  # DELETE arrived mid-stage: write nothing (§6.7)
            await s.execute(
                text(
                    "UPDATE processing_jobs SET status = 'dead', lease_owner = NULL, "
                    "lease_expires_at = NULL, error_code = 'deleted', error_detail = NULL WHERE id = :id"
                ),
                {"id": job.id},
            )
            await s.commit()
            return "deleted"
        if result.write is not None:
            await result.write(s)
        if result.terminal_status is not None:
            await vs.set_version_status(s, job.version_id, result.terminal_status)
        if nxt is None:
            await s.execute(
                text(
                    "UPDATE processing_jobs SET status = 'succeeded', lease_owner = NULL, "
                    "lease_expires_at = NULL, error_code = NULL, error_detail = NULL WHERE id = :id"
                ),
                {"id": job.id},
            )
        else:
            await s.execute(
                text(
                    "UPDATE processing_jobs SET status = 'pending', current_stage = :next, "
                    "attempt_count = 0, next_attempt_at = NULL, lease_owner = NULL, lease_expires_at = NULL, "
                    "last_dispatched_at = NULL, error_code = NULL, error_detail = NULL WHERE id = :id"
                ),
                {"id": job.id, "next": nxt},
            )
        await _event(s, job, stage, "succeeded", job.attempt_count + 1, ms, result.detail)
        await s.commit()
    await publish_wake(get_settings().redis_url, job.version_id)  # live status; never raises
    if nxt is None:
        return "finished"
    async with sm() as s:
        await dispatch(s, job.id, enqueue)
    return "advanced"


async def _fail(
    sm: async_sessionmaker[AsyncSession],
    job: ClaimedJob,
    worker: str,
    err: StageError,
    ms: int,
    rng: Callable[[], float],
) -> Outcome:
    attempt = job.attempt_count + 1
    terminal = err.permanent or attempt >= job.max_attempts
    detail = {"error_code": err.code, "permanent": err.permanent}
    async with sm() as s:
        if not await _fence(s, job, worker):
            await s.rollback()
            return "lost_lease"
        if terminal:
            version_status = err.version_status if err.permanent else vs.FAILED
            await vs.set_version_status(
                s, job.version_id, version_status, error_code=err.code, error_detail=err.detail
            )
            await s.execute(
                text(
                    "UPDATE processing_jobs SET status = 'dead', attempt_count = :a, lease_owner = NULL, "
                    "lease_expires_at = NULL, error_code = :c, error_detail = :d WHERE id = :id"
                ),
                {"id": job.id, "a": attempt, "c": err.code, "d": err.detail},
            )
        else:
            await s.execute(
                text(
                    "UPDATE processing_jobs SET status = 'failed', attempt_count = :a, "
                    "next_attempt_at = now() + make_interval(secs => :b), lease_owner = NULL, "
                    "lease_expires_at = NULL, last_dispatched_at = NULL, error_code = :c, error_detail = :d "
                    "WHERE id = :id"
                ),
                {"id": job.id, "a": attempt, "b": backoff_s(attempt, rng), "c": err.code, "d": err.detail},
            )
        await _event(s, job, job.current_stage, "error", attempt, ms, detail)
        await s.commit()
    await publish_wake(get_settings().redis_url, job.version_id)  # live status; never raises
    _log.info(
        "stage_failed",
        job_id=str(job.id),
        stage=job.current_stage,
        error_code=err.code,
        attempt=attempt,
        terminal=terminal,
    )
    return "dead" if terminal else "retry"
