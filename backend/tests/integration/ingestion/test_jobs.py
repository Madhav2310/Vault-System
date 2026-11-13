"""S1.08 claim, lease and heartbeat."""

import asyncio
import datetime as dt

from sqlalchemy import text

from app.ingestion.jobs import claim, heartbeat
from app.ingestion.runner import StageContext, StageResult, run_job
from tests.support.jobs import events, expire_lease, job_row, make_job


async def _now(sm) -> dt.datetime:  # type: ignore[no-untyped-def]
    async with sm() as s:
        return (await s.execute(text("SELECT now()"))).scalar_one()


async def test_claim_sets_lease(sm, make_owner) -> None:  # type: ignore[no-untyped-def]
    ref = await make_job(sm, (await make_owner()).id)
    async with sm() as s:
        job = await claim(s, ref.job_id, "w1")
    assert job is not None and job.current_stage == "extract"
    row = await job_row(sm, ref.job_id)
    assert row["status"] == "running" and row["lease_owner"] == "w1"
    delta = (row["lease_expires_at"] - await _now(sm)).total_seconds()  # type: ignore[operator]
    assert 290 < delta <= 300


async def test_claim_blocked_by_live_lease(sm, make_owner) -> None:  # type: ignore[no-untyped-def]
    ref = await make_job(sm, (await make_owner()).id)
    async with sm() as s:
        assert await claim(s, ref.job_id, "w1") is not None
    async with sm() as s:
        assert await claim(s, ref.job_id, "w2") is None
    assert (await job_row(sm, ref.job_id))["lease_owner"] == "w1"


async def test_claim_after_expiry(sm, make_owner) -> None:  # type: ignore[no-untyped-def]
    ref = await make_job(sm, (await make_owner()).id)
    async with sm() as s:
        await claim(s, ref.job_id, "w1")
    await expire_lease(sm, ref.job_id)
    async with sm() as s:
        assert await claim(s, ref.job_id, "w2") is not None
    assert (await job_row(sm, ref.job_id))["lease_owner"] == "w2"


async def test_heartbeat_extends(sm, make_owner) -> None:  # type: ignore[no-untyped-def]
    ref = await make_job(sm, (await make_owner()).id)
    async with sm() as s:
        await claim(s, ref.job_id, "w1", lease_s=5)
    before = (await job_row(sm, ref.job_id))["lease_expires_at"]
    async with sm() as s:
        assert await heartbeat(s, ref.job_id, "w1", lease_s=300) is True
        assert await heartbeat(s, ref.job_id, "intruder") is False
    after = (await job_row(sm, ref.job_id))["lease_expires_at"]
    assert (after - before).total_seconds() > 290  # type: ignore[operator]


class SlowStage:
    name = "extract"

    def __init__(self) -> None:
        self.runs = 0

    async def run(self, ctx: StageContext) -> StageResult:
        self.runs += 1
        await asyncio.sleep(0.3)
        return StageResult()


async def test_duplicate_delivery_noop(sm, make_owner, enqueued) -> None:  # type: ignore[no-untyped-def]
    ref = await make_job(sm, (await make_owner()).id)
    stage = SlowStage()
    stages = {"extract": stage}
    outcomes = await asyncio.gather(
        run_job(sm, ref.job_id, "w1", stages, enqueued, order=("extract",)),
        run_job(sm, ref.job_id, "w2", stages, enqueued, order=("extract",)),
    )
    assert sorted(outcomes) == ["finished", "noop"]
    assert stage.runs == 1
    assert await events(sm, ref.job_id) == [("extract", "succeeded", 1)]
