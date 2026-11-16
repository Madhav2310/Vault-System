"""Maintenance tasks purge expired idempotency keys and old job events."""

import uuid

from sqlalchemy import text

from app.worker.tasks.maintenance import purge_idempotency_keys


async def test_idempotency_purge(sm, make_owner) -> None:  # type: ignore[no-untyped-def]
    o = await make_owner()
    async with sm() as s:
        for key, age_h in (("old", 25), ("edge", 23), ("new", 0)):
            await s.execute(
                text(
                    "INSERT INTO idempotency_keys (owner_id, key, request_hash, created_at) "
                    "VALUES (:o, :k, 'h', now() - make_interval(hours => :a))"
                ),
                {"o": o.id, "k": key, "a": age_h},
            )
        await s.commit()
    deleted = await purge_idempotency_keys(sm)
    async with sm() as s:
        left = (
            (
                await s.execute(
                    text("SELECT key FROM idempotency_keys WHERE owner_id = :o ORDER BY key"), {"o": o.id}
                )
            )
            .scalars()
            .all()
        )
    assert left == ["edge", "new"] and deleted >= 1


async def test_job_events_purge(sm, make_owner) -> None:  # type: ignore[no-untyped-def]
    from app.worker.tasks.maintenance import purge_job_events
    from tests.support.jobs import make_job

    ref = await make_job(sm, (await make_owner()).id)
    async with sm() as s:
        for stage, age_d in (("old", 91), ("recent", 89)):
            await s.execute(
                text(
                    "INSERT INTO job_events (id, job_id, stage, status, attempt, created_at) "
                    "VALUES (:id, :j, :st, 'succeeded', 1, now() - make_interval(days => :a))"
                ),
                {"id": uuid.uuid4(), "j": ref.job_id, "st": stage, "a": age_d},
            )
        await s.commit()
    await purge_job_events(sm)
    async with sm() as s:
        left = (
            (await s.execute(text("SELECT stage FROM job_events WHERE job_id = :j"), {"j": ref.job_id}))
            .scalars()
            .all()
        )
    assert left == ["recent"]
