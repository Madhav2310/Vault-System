"""Regression tests for the C1 review findings on the stage runner.

Covers poison jobs, lease takeover and heartbeat handling."""

import asyncio

from sqlalchemy import text

from app.ingestion.jobs import claim
from app.ingestion.runner import StageContext, StageResult, run_job
from tests.support.jobs import events, expire_lease, job_row, make_job, version_status


class BadWrite:
    """Computes fine, but its commit-phase write raises (bad data, deadlock, illegal transition...)."""

    name = "extract"

    async def run(self, ctx: StageContext) -> StageResult:
        async def write(s):  # type: ignore[no-untyped-def]
            await s.execute(text("SELECT 1/0"))

        return StageResult(write=write)


async def test_commit_failure_is_counted_and_ends_dead(sm, make_owner, enqueued) -> None:  # type: ignore[no-untyped-def]
    ref = await make_job(sm, (await make_owner()).id)
    outcomes = []
    for _ in range(5):
        async with sm() as s:
            await s.execute(
                text("UPDATE processing_jobs SET status = 'pending', next_attempt_at = NULL WHERE id = :j"),
                {"j": ref.job_id},
            )
            await s.commit()
        outcomes.append(
            await run_job(sm, ref.job_id, "w", {"extract": BadWrite()}, enqueued, order=("extract",))
        )
    # before the fix the job stayed `running` with attempt_count 0 forever; now it is retried and then dead
    assert outcomes == ["retry"] * 4 + ["dead"]
    row = await job_row(sm, ref.job_id)
    assert (row["status"], row["attempt_count"], row["error_code"]) == ("dead", 5, "commit_failed")
    assert await version_status(sm, ref.version_id) == ("failed", "commit_failed")
    assert [e[1] for e in await events(sm, ref.job_id)] == ["error"] * 5


class Fine:
    name = "extract"

    async def run(self, ctx: StageContext) -> StageResult:
        return StageResult()


async def test_lease_takeover_counts_attempts_then_dead(sm, make_owner, enqueued) -> None:  # type: ignore[no-untyped-def]
    """A stage that keeps killing its worker (OOM) used to loop behind the sweeper forever."""
    ref = await make_job(sm, (await make_owner()).id)
    for n in range(1, 5):
        async with sm() as s:
            assert await claim(s, ref.job_id, f"crashing-{n}") is not None  # worker dies holding the lease
        await expire_lease(sm, ref.job_id)
    row = await job_row(sm, ref.job_id)
    assert row["attempt_count"] == 3  # the 2nd, 3rd and 4th claims were takeovers of a running job
    async with (
        sm() as s
    ):  # the 5th takeover reaches max_attempts: the runner kills the job instead of running it
        await s.execute(text("UPDATE processing_jobs SET max_attempts = 4 WHERE id = :j"), {"j": ref.job_id})
        await s.commit()
    out = await run_job(sm, ref.job_id, "w", {"extract": Fine()}, enqueued, order=("extract",))
    assert out == "dead"
    assert (await job_row(sm, ref.job_id))["error_code"] == "worker_lost"
    assert (await version_status(sm, ref.version_id)) == ("failed", "worker_lost")


class Slow:
    name = "extract"

    def __init__(self) -> None:
        self.finished = False
        self.cancelled = False

    async def run(self, ctx: StageContext) -> StageResult:
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        self.finished = True
        return StageResult()


async def test_lost_lease_cancels_the_stage(sm, make_owner, enqueued) -> None:  # type: ignore[no-untyped-def]
    ref = await make_job(sm, (await make_owner()).id)
    stage = Slow()

    async def steal() -> None:
        await asyncio.sleep(0.25)
        async with sm() as s:  # another worker takes over (lease expired from its point of view)
            await s.execute(
                text("UPDATE processing_jobs SET lease_owner = 'thief' WHERE id = :j"), {"j": ref.job_id}
            )
            await s.commit()

    out, _ = await asyncio.gather(
        run_job(sm, ref.job_id, "w", {"extract": stage}, enqueued, order=("extract",), heartbeat_s=0.1),
        steal(),
    )
    assert out == "lost_lease" and stage.cancelled and not stage.finished  # stopped, not left spending money
    assert await events(sm, ref.job_id) == []  # and it committed nothing


async def test_heartbeat_error_does_not_mask_the_result(sm, make_owner, enqueued, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.ingestion import runner

    async def boom(*a, **kw):  # type: ignore[no-untyped-def]
        raise RuntimeError("db hiccup")

    monkeypatch.setattr(runner, "heartbeat", boom)
    ref = await make_job(sm, (await make_owner()).id)

    class Short:
        name = "extract"

        async def run(self, ctx: StageContext) -> StageResult:
            await asyncio.sleep(0.4)
            return StageResult()

    out = await run_job(
        sm, ref.job_id, "w", {"extract": Short()}, enqueued, order=("extract",), heartbeat_s=0.05
    )
    assert out == "finished"  # a failing heartbeat neither aborts the stage nor loses its result
