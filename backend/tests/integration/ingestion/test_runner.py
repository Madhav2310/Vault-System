"""Stage runner: commit, idempotent re-run, retries, permanent errors, dead state."""

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.errors import PermanentStageError, RetryableStageError, UnsupportedContentError
from app.ingestion.runner import BACKOFF_BASE_S, JITTER, StageContext, StageResult, run_job
from tests.support.jobs import events, job_row, make_job, version_status

ORDER = ("extract", "quality")


class PagesStage:
    """Writes `pages` rows for the version (delete + insert, the stage-output pattern)."""

    name = "extract"

    def __init__(self, n_pages: int = 3) -> None:
        self.n = n_pages

    async def run(self, ctx: StageContext) -> StageResult:
        v = ctx.job.version_id

        async def write(s: AsyncSession) -> None:
            await s.execute(text("DELETE FROM pages WHERE version_id = :v"), {"v": v})
            for p in range(1, self.n + 1):
                await s.execute(
                    text(
                        "INSERT INTO pages (version_id, page_no, width, height, text_chars) "
                        "VALUES (:v, :p, 612, 792, 10)"
                    ),
                    {"v": v, "p": p},
                )

        return StageResult(write=write, detail={"pages": self.n})


class Failing:
    name = "extract"

    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    async def run(self, ctx: StageContext) -> StageResult:
        raise self.exc


async def _pages(sm, v: uuid.UUID) -> int:  # type: ignore[no-untyped-def]
    async with sm() as s:
        return (
            await s.execute(text("SELECT count(*) FROM pages WHERE version_id = :v"), {"v": v})
        ).scalar_one()


async def _cleanup_pages(sm, v: uuid.UUID) -> None:  # type: ignore[no-untyped-def]
    async with sm() as s:
        await s.execute(text("DELETE FROM pages WHERE version_id = :v"), {"v": v})
        await s.commit()


async def test_stage_success_event(sm, make_owner, enqueued) -> None:  # type: ignore[no-untyped-def]
    ref = await make_job(sm, (await make_owner()).id)
    out = await run_job(sm, ref.job_id, "w", {"extract": PagesStage()}, enqueued, order=ORDER)
    assert out == "advanced"
    async with sm() as s:
        ms = (
            await s.execute(text("SELECT duration_ms FROM job_events WHERE job_id = :j"), {"j": ref.job_id})
        ).scalar_one()
    assert await events(sm, ref.job_id) == [("extract", "succeeded", 1)] and ms is not None and ms >= 0
    row = await job_row(sm, ref.job_id)
    assert row["current_stage"] == "quality" and row["status"] == "dispatched" and row["lease_owner"] is None
    assert enqueued.calls == [("quality", ref.job_id)]
    assert (await version_status(sm, ref.version_id))[0] == "processing"
    await _cleanup_pages(sm, ref.version_id)


async def test_stage_rerun_idempotent(sm, make_owner, enqueued) -> None:  # type: ignore[no-untyped-def]
    ref = await make_job(sm, (await make_owner()).id)
    await run_job(sm, ref.job_id, "w", {"extract": PagesStage()}, enqueued, order=ORDER)
    assert await _pages(sm, ref.version_id) == 3
    async with sm() as s:  # redelivery after the stage committed (e.g. a replayed message): run it again
        await s.execute(
            text("UPDATE processing_jobs SET current_stage = 'extract', status = 'pending' WHERE id = :j"),
            {"j": ref.job_id},
        )
        await s.commit()
    await run_job(sm, ref.job_id, "w", {"extract": PagesStage()}, enqueued, order=ORDER)
    assert await _pages(sm, ref.version_id) == 3
    await _cleanup_pages(sm, ref.version_id)


async def test_retryable_backoff(sm, make_owner, enqueued) -> None:  # type: ignore[no-untyped-def]
    ref = await make_job(sm, (await make_owner()).id)
    async with sm() as s:
        t0 = (await s.execute(text("SELECT now()"))).scalar_one()
    out = await run_job(
        sm, ref.job_id, "w", {"extract": Failing(RetryableStageError("timeout"))}, enqueued, order=ORDER
    )
    assert out == "retry"
    row = await job_row(sm, ref.job_id)
    assert row["status"] == "failed" and row["attempt_count"] == 1 and row["error_code"] == "timeout"
    wait = (row["next_attempt_at"] - t0).total_seconds()  # type: ignore[operator]
    base = BACKOFF_BASE_S * 2  # attempt 1
    assert base * (1 - JITTER) - 1 <= wait <= base * (1 + JITTER) + 1
    assert row["lease_owner"] is None and row["last_dispatched_at"] is None
    assert (await version_status(sm, ref.version_id))[0] == "processing"
    assert await events(sm, ref.job_id) == [("extract", "error", 1)]
    # an unexpected exception is retried too, as `internal`
    async with sm() as s:
        await s.execute(
            text("UPDATE processing_jobs SET status = 'pending' WHERE id = :j"), {"j": ref.job_id}
        )
        await s.commit()
    assert (
        await run_job(sm, ref.job_id, "w", {"extract": Failing(KeyError("bug"))}, enqueued, order=ORDER)
        == "retry"
    )
    assert (await job_row(sm, ref.job_id))["error_code"] == "internal"


async def test_permanent_error_terminal(sm, make_owner, enqueued) -> None:  # type: ignore[no-untyped-def]
    o = await make_owner()
    for exc, version_status_expected in (
        (UnsupportedContentError("encrypted"), "unsupported"),
        (PermanentStageError("corrupt_file"), "failed"),
    ):
        ref = await make_job(sm, o.id)
        assert await run_job(sm, ref.job_id, "w", {"extract": Failing(exc)}, enqueued, order=ORDER) == "dead"
        row = await job_row(sm, ref.job_id)
        assert row["status"] == "dead" and row["attempt_count"] == 1 and row["error_code"] == exc.code
        assert await version_status(sm, ref.version_id) == (version_status_expected, exc.code)


async def test_dead_after_max_attempts(sm, make_owner, enqueued) -> None:  # type: ignore[no-untyped-def]
    ref = await make_job(sm, (await make_owner()).id)
    stages = {"extract": Failing(RetryableStageError("timeout"))}
    outcomes = []
    for _ in range(5):
        async with sm() as s:  # make the retry claimable now (the sweeper would after next_attempt_at)
            await s.execute(
                text("UPDATE processing_jobs SET status = 'pending', next_attempt_at = NULL WHERE id = :j"),
                {"j": ref.job_id},
            )
            await s.commit()
        outcomes.append(await run_job(sm, ref.job_id, "w", stages, enqueued, order=ORDER))
    assert outcomes == ["retry"] * 4 + ["dead"]
    row = await job_row(sm, ref.job_id)
    assert row["status"] == "dead" and row["attempt_count"] == 5 and row["error_code"] == "timeout"
    assert await version_status(sm, ref.version_id) == ("failed", "timeout")
