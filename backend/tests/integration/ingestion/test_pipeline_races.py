"""Races and recovery paths of the job pipeline: sweeper takeovers, post-ready failures, purge vs a commit."""

import asyncio
import pathlib
import uuid

from sqlalchemy import text

from app.core.settings import get_settings
from app.documents.delete import purge_document
from app.ingestion.errors import PermanentStageError
from app.ingestion.jobs import claim
from app.ingestion.runner import StageContext, StageResult, run_job
from app.ingestion.stages import ExtractStage
from app.ingestion.sweeper import sweep
from app.storage.local import LocalObjectStore
from tests.support.jobs import expire_lease, job_row, make_job, version_status


async def test_sweeper_takeover_of_a_dead_worker_counts_an_attempt(sm, make_owner, enqueued) -> None:  # type: ignore[no-untyped-def]
    """The production recovery path for a worker killed mid-stage (OOM): its lease expires, the sweeper
    re-dispatches, a new worker claims. That takeover must count, or a poison stage loops forever."""
    ref = await make_job(sm, (await make_owner()).id)
    async with sm() as s:
        assert await claim(s, ref.job_id, "crashing-1") is not None  # dies holding the lease
    await expire_lease(sm, ref.job_id)
    async with sm() as s:
        await s.execute(
            text(
                "UPDATE processing_jobs SET last_dispatched_at = now() - interval '6 minutes' WHERE id = :j"
            ),
            {"j": ref.job_id},
        )
        await s.commit()
    assert ref.job_id in await sweep(sm, enqueued)
    async with sm() as s:
        assert await claim(s, ref.job_id, "crashing-2") is not None
    assert (await job_row(sm, ref.job_id))["attempt_count"] == 1


class FailsForGood:
    name = "brief"

    async def run(self, ctx: StageContext) -> StageResult:
        raise PermanentStageError("boom", "enrichment cannot succeed")


async def test_terminal_failure_after_ready_keeps_the_version_ready(sm, make_owner, enqueued) -> None:  # type: ignore[no-untyped-def]
    """An enrichment stage (after `ready`) that ends terminally kills the job; the searchable version stays
    `ready` instead of the failure handler raising an illegal `ready -> failed` transition."""
    ref = await make_job(sm, (await make_owner()).id, stage="brief")
    async with sm() as s:
        await s.execute(
            text("UPDATE document_versions SET status = 'ready' WHERE id = :v"), {"v": ref.version_id}
        )
        await s.commit()
    out = await run_job(
        sm, ref.job_id, "w", {"brief": FailsForGood()}, enqueued, order=("index", "brief", "contextualize")
    )
    assert out == "dead"
    row = await job_row(sm, ref.job_id)
    assert (row["status"], row["error_code"], row["lease_owner"]) == ("dead", "boom", None)
    assert (await version_status(sm, ref.version_id))[0] == "ready"


class DeleteDuringPut(LocalObjectStore):
    """When the extract stage writes its artifact inside the fenced commit, the document is deleted and its
    purge starts; the put goes ahead once the purge is blocked on the job row this commit holds."""

    def __init__(self, root: pathlib.Path, sm, document_id: uuid.UUID) -> None:  # type: ignore[no-untyped-def]
        super().__init__(root)
        self.sm, self.document_id = sm, document_id
        self.purge: asyncio.Task[int] | None = None

    async def put(self, key: str, data: bytes) -> None:
        if key.startswith("extracted/") and self.purge is None:
            async with self.sm() as s:
                await s.execute(
                    text("UPDATE documents SET deleted_at = now() WHERE id = :d"), {"d": self.document_id}
                )
                await s.commit()
            self.purge = asyncio.ensure_future(purge_document(self.sm, self, self.document_id))
            for _ in range(200):  # until the purge waits on our lock
                async with self.sm() as s:
                    waiting = (
                        await s.execute(
                            text(
                                "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
                                "AND wait_event_type = 'Lock'"
                            )
                        )
                    ).scalar_one()
                if waiting:
                    break
                await asyncio.sleep(0.02)
            assert waiting, "the purge never reached the job-row lock"
        await super().put(key, data)


async def test_purge_racing_an_extract_commit_leaves_no_artifact(sm, make_owner, enqueued, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A delete that lands while the extract stage commits must not leave `extracted/{version}` behind:
    the purge marks the version purged, so nothing would ever remove the leftover document text."""
    ref = await make_job(sm, (await make_owner()).id)
    async with sm() as s:
        sha = (
            await s.execute(text("SELECT sha256 FROM document_versions WHERE id = :v"), {"v": ref.version_id})
        ).scalar_one()
    store = DeleteDuringPut(tmp_path / "blobs", sm, ref.document_id)
    await LocalObjectStore.put(store, f"blobs/{sha}", b"The supplier shall deliver the goods by May.\n")
    services = {"store": store, "settings": get_settings()}
    await run_job(sm, ref.job_id, "w", {"extract": ExtractStage()}, enqueued, services=services)
    assert store.purge is not None
    assert await store.purge == 1
    async with sm() as s:
        purged = (
            await s.execute(
                text("SELECT count(*) FROM document_versions WHERE id = :v"), {"v": ref.version_id}
            )
        ).scalar_one()
    assert purged == 0
    assert not await store.exists(f"extracted/{ref.version_id}.json.gz")
