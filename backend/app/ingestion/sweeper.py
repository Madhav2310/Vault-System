"""Sweeper: eventual recovery of lost dispatches, dead workers and due retries.

Runs from beat every 60 s. It only *selects candidates*; the dispatch CAS decides, so concurrent sweepers
(or a sweeper racing a normal dispatch) still enqueue each job at most once per grace period.
"""

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import get_logger
from app.ingestion.jobs import DISPATCH_GRACE_S, Enqueue, dispatch

BATCH = 500
_log = get_logger("vault.ingestion.sweeper")

CANDIDATES = text(
    "SELECT id FROM processing_jobs WHERE "
    # (a) pending/dispatched past the grace period with no live lease (lost enqueue or lost message)
    "(status IN ('pending', 'dispatched') "
    " AND COALESCE(last_dispatched_at, created_at) < now() - make_interval(secs => :g) "
    " AND (lease_expires_at IS NULL OR lease_expires_at < now())) "
    # (b) running with an expired lease (the worker died mid-stage)
    "OR (status = 'running' AND lease_expires_at < now()) "
    # (c) retry due
    "OR (status = 'failed' AND next_attempt_at <= now()) "
    "ORDER BY created_at LIMIT :n"
)


async def sweep(
    sm: async_sessionmaker[AsyncSession], enqueue: Enqueue, grace_s: int = DISPATCH_GRACE_S
) -> list[uuid.UUID]:
    async with sm() as s:
        ids = list((await s.execute(CANDIDATES, {"g": grace_s, "n": BATCH})).scalars())
    won: list[uuid.UUID] = []
    for job_id in ids:
        async with sm() as s:
            if await dispatch(s, job_id, enqueue, grace_s):
                won.append(job_id)
    if ids:
        _log.info("sweep", candidates=len(ids), dispatched=len(won))
    return won
