"""S1.10 sweeper."""

import asyncio

from sqlalchemy import text

from app.ingestion.jobs import claim, dispatch
from app.ingestion.sweeper import sweep
from tests.support.jobs import expire_lease, make_job


async def _age(sm, job_id, **cols: str) -> None:  # type: ignore[no-untyped-def]
    sets = ", ".join(f"{k} = {v}" for k, v in cols.items())
    async with sm() as s:
        await s.execute(text(f"UPDATE processing_jobs SET {sets} WHERE id = :j"), {"j": job_id})
        await s.commit()


async def test_lost_enqueue_recovered(sm, make_owner, enqueued) -> None:  # type: ignore[no-untyped-def]
    ref = await make_job(sm, (await make_owner()).id)
    assert ref.job_id not in await sweep(sm, enqueued)  # fresh: inside the grace period
    await _age(sm, ref.job_id, created_at="now() - interval '6 minutes'")
    assert ref.job_id in await sweep(sm, enqueued)
    assert ("extract", ref.job_id) in enqueued.calls


async def test_expired_lease_recovered(sm, make_owner, enqueued) -> None:  # type: ignore[no-untyped-def]
    ref = await make_job(sm, (await make_owner()).id)
    async with sm() as s:
        await dispatch(s, ref.job_id, enqueued)
    async with sm() as s:
        await claim(s, ref.job_id, "dead-worker")
    await _age(sm, ref.job_id, last_dispatched_at="now() - interval '6 minutes'")
    await expire_lease(sm, ref.job_id)
    assert ref.job_id in await sweep(sm, enqueued)


async def test_retry_due_only(sm, make_owner, enqueued) -> None:  # type: ignore[no-untyped-def]
    o = await make_owner()
    due, later = await make_job(sm, o.id, status="failed"), await make_job(sm, o.id, status="failed")
    await _age(sm, due.job_id, next_attempt_at="now() - interval '1 second'")
    await _age(sm, later.job_id, next_attempt_at="now() + interval '10 minutes'")
    won = await sweep(sm, enqueued)
    assert due.job_id in won and later.job_id not in won


async def test_live_lease_untouched(sm, make_owner, enqueued) -> None:  # type: ignore[no-untyped-def]
    ref = await make_job(sm, (await make_owner()).id)
    async with sm() as s:
        await claim(s, ref.job_id, "busy-worker")
    await _age(
        sm, ref.job_id, created_at="now() - interval '1 hour'", last_dispatched_at="now() - interval '1 hour'"
    )
    assert ref.job_id not in await sweep(sm, enqueued)


async def test_concurrent_sweepers(sm, make_owner, enqueued) -> None:  # type: ignore[no-untyped-def]
    o = await make_owner()
    refs = [await make_job(sm, o.id) for _ in range(5)]
    for r in refs:
        await _age(sm, r.job_id, created_at="now() - interval '6 minutes'")
    await asyncio.gather(*(sweep(sm, enqueued) for _ in range(4)))
    ids = [j for _, j in enqueued.calls if j in {r.job_id for r in refs}]
    assert sorted(ids) == sorted(r.job_id for r in refs)  # each exactly once
