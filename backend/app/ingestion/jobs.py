"""Job dispatch, claim, lease and heartbeat (design §6.6, §14).

- dispatch: compare-and-set on `processing_jobs`; only the winner enqueues; the payload is the job id only.
- claim: a worker takes a lease (5 min) before doing stage work; a live lease turns duplicates into no-ops.
- heartbeat: a long stage extends its lease every 60 s; a lost lease means another worker may own the job.

Stage commit and failure clear the lease and `last_dispatched_at` (see runner), so the next stage or the retry
can be dispatched at once; the grace period only stops redelivery of a dispatch still in flight.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

LEASE_S = 300
HEARTBEAT_S = 60
DISPATCH_GRACE_S = 300

# stage → Celery task (and therefore queue); see app/worker/celery_app.py ROUTES
STAGE_TASKS: dict[str, str] = {
    "extract": "vault.ingest.stage",
    "quality": "vault.ingest.stage",
    "chunk": "vault.ingest.stage",
    "embed": "vault.ai.stage",
    "index": "vault.ai.stage",
}

Enqueue = Callable[[str, uuid.UUID], Awaitable[None]]  # (stage, job_id)


@dataclass(frozen=True)
class ClaimedJob:
    id: uuid.UUID
    version_id: uuid.UUID
    owner_id: uuid.UUID
    current_stage: str
    attempt_count: int
    max_attempts: int


async def dispatch(
    session: AsyncSession, job_id: uuid.UUID, enqueue: Enqueue, grace_s: int = DISPATCH_GRACE_S
) -> bool:
    """CAS-dispatch one job; True if this caller won and enqueued. Commits its own short transaction."""
    row = (
        await session.execute(
            text(
                "UPDATE processing_jobs SET status = 'dispatched', last_dispatched_at = now() "
                "WHERE id = :id AND status IN ('pending', 'dispatched', 'running', 'failed') "
                "AND (last_dispatched_at IS NULL OR last_dispatched_at < now() - make_interval(secs => :g)) "
                "AND (lease_expires_at IS NULL OR lease_expires_at < now()) "
                "AND (next_attempt_at IS NULL OR next_attempt_at <= now()) "
                "RETURNING current_stage"
            ),
            {"id": job_id, "g": grace_s},
        )
    ).first()
    await session.commit()
    if row is None:
        return False
    await enqueue(row.current_stage, job_id)
    return True


async def claim(
    session: AsyncSession, job_id: uuid.UUID, worker: str, lease_s: int = LEASE_S
) -> ClaimedJob | None:
    row = (
        await session.execute(
            text(
                "UPDATE processing_jobs SET status = 'running', lease_owner = :w, "
                "attempt_count = attempt_count + CASE WHEN status = 'running' THEN 1 ELSE 0 END, "
                "lease_expires_at = now() + make_interval(secs => :l) "
                "WHERE id = :id AND status IN ('pending', 'dispatched', 'running') "
                "AND (lease_expires_at IS NULL OR lease_expires_at < now()) "
                "RETURNING id, version_id, owner_id, current_stage, attempt_count, max_attempts"
            ),
            {"id": job_id, "w": worker, "l": lease_s},
        )
    ).first()
    await session.commit()
    return ClaimedJob(*row) if row is not None else None


async def heartbeat(session: AsyncSession, job_id: uuid.UUID, worker: str, lease_s: int = LEASE_S) -> bool:
    """Extend our lease; False if it is no longer ours."""
    row = (
        await session.execute(
            text(
                "UPDATE processing_jobs SET lease_expires_at = now() + make_interval(secs => :l) "
                "WHERE id = :id AND lease_owner = :w AND status = 'running' RETURNING id"
            ),
            {"id": job_id, "w": worker, "l": lease_s},
        )
    ).first()
    await session.commit()
    return row is not None


def celery_enqueue() -> Enqueue:
    """Production enqueuer: Celery `send_task` (a blocking Redis write) off the event loop."""
    from app.worker.celery_app import celery_app

    async def enqueue(stage: str, job_id: uuid.UUID) -> None:
        await asyncio.to_thread(celery_app.send_task, STAGE_TASKS[stage], args=[str(job_id)])

    return enqueue
