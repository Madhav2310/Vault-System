"""S1.26 soft delete, purge, refcounted blob removal, and abort of in-flight stages."""

import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import get_db, get_enqueue, get_store
from app.core.ids import uuid7
from app.documents.delete import get_purge_enqueue, purge_document
from app.ingestion.runner import StageContext, StageResult, run_job
from app.main import create_app
from tests.support.docs import (
    Seeded,
    clear_owner_rows,
    put_gz_json,
    seed_chunk,
    seed_document,
    seed_insight,
    seed_pages,
)


class PurgeRecorder:
    def __init__(self) -> None:
        self.calls: list[uuid.UUID] = []

    async def __call__(self, document_id: uuid.UUID) -> None:
        self.calls.append(document_id)


@pytest.fixture
def purges() -> PurgeRecorder:
    return PurgeRecorder()


@pytest.fixture
async def api(  # overrides the shared fixture to also stub the purge enqueue
    sm: async_sessionmaker[AsyncSession], store: Any, enqueued: Any, purges: PurgeRecorder
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()

    async def _db() -> AsyncIterator[AsyncSession]:
        async with sm() as s:
            yield s

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_enqueue] = lambda: enqueued
    app.dependency_overrides[get_purge_enqueue] = lambda: purges
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=60) as c:
        yield c


@pytest.fixture
async def owners(make_owner: Callable[[], Any], sm: Any) -> AsyncIterator[Callable[[], Any]]:
    made: list[uuid.UUID] = []

    async def mk() -> Any:
        o = await make_owner()
        made.append(o.id)
        return o

    yield mk
    await clear_owner_rows(sm, made)


async def _one(sm: Any, sql: str, **p: Any) -> Any:
    async with sm() as s:
        return (await s.execute(text(sql), p)).scalar_one_or_none()


async def _mark_deleted(sm: Any, document_id: uuid.UUID) -> None:
    async with sm() as s:
        await s.execute(text("UPDATE documents SET deleted_at = now() WHERE id = :d"), {"d": document_id})
        await s.commit()


async def _full_doc(sm: Any, store: Any, owner_id: uuid.UUID, **kw: Any) -> tuple[Seeded, uuid.UUID]:
    """A document with a chunk, insight, pages, a job event, and layout + extracted objects."""
    s = await seed_document(sm, owner_id, store=store, **kw)
    cid = await seed_chunk(sm, owner_id, s)
    await seed_insight(sm, owner_id, s.version_id)
    await seed_pages(sm, s.version_id, {1: []})
    async with sm() as db:
        await db.execute(
            text(
                "INSERT INTO job_events (id, job_id, stage, status, attempt) "
                "VALUES (:i, :j, 'extract', 'succeeded', 1)"
            ),
            {"i": uuid7(), "j": s.job_id},
        )
        await db.commit()
    await put_gz_json(store, f"layouts/{s.version_id}.json.gz", {"1": {"w": 1, "h": 1, "glyphs": []}})
    await put_gz_json(store, f"extracted/{s.version_id}.json.gz", {"pages": []})
    return s, cid


async def test_soft_delete(api: httpx.AsyncClient, owners, sm, purges) -> None:  # type: ignore[no-untyped-def]
    o = await owners()
    s = await seed_document(sm, o.id)
    r = await api.delete(f"/v1/documents/{s.document_id}", headers=o.auth)
    assert r.status_code == 204 and r.content == b""
    assert await _one(sm, "SELECT deleted_at IS NOT NULL FROM documents WHERE id = :d", d=s.document_id)
    assert purges.calls == [s.document_id]
    # a second delete finds nothing (the document is gone from the owner's view)
    assert (await api.delete(f"/v1/documents/{s.document_id}", headers=o.auth)).status_code == 404
    assert purges.calls == [s.document_id]


async def test_excluded_from_list(api: httpx.AsyncClient, owners, sm) -> None:  # type: ignore[no-untyped-def]
    o = await owners()
    keep = await seed_document(sm, o.id, title="keep")
    gone = await seed_document(sm, o.id, title="gone")
    assert (await api.delete(f"/v1/documents/{gone.document_id}", headers=o.auth)).status_code == 204
    r = await api.get("/v1/documents", headers=o.auth)
    assert [d["id"] for d in r.json()["items"]] == [str(keep.document_id)]
    assert (await api.get(f"/v1/documents/{gone.document_id}", headers=o.auth)).status_code == 404
    assert (await api.get(f"/v1/versions/{gone.version_id}/processing", headers=o.auth)).status_code == 404


async def test_purge_artifacts(owners, sm, store) -> None:  # type: ignore[no-untyped-def]
    o = await owners()
    s, cid = await _full_doc(sm, store, o.id)
    assert await purge_document(sm, store, s.document_id) == 0  # not deleted yet: refuses to purge
    assert await _one(sm, "SELECT count(*) FROM chunks WHERE id = :c", c=cid) == 1
    await _mark_deleted(sm, s.document_id)
    assert await purge_document(sm, store, s.document_id) == 1
    for tbl, col in (
        ("chunks", "version_id"),
        ("document_insights", "version_id"),
        ("pages", "version_id"),
        ("processing_jobs", "version_id"),
    ):
        assert await _one(sm, f"SELECT count(*) FROM {tbl} WHERE {col} = :v", v=s.version_id) == 0, tbl
    assert await _one(sm, "SELECT count(*) FROM job_events WHERE job_id = :j", j=s.job_id) == 0
    assert not await store.exists(f"layouts/{s.version_id}.json.gz")
    assert not await store.exists(f"extracted/{s.version_id}.json.gz")
    # the document row stays (soft-deleted); its unreferenced version row is gone
    assert await _one(sm, "SELECT count(*) FROM documents WHERE id = :d", d=s.document_id) == 1
    assert await _one(sm, "SELECT count(*) FROM document_versions WHERE id = :v", v=s.version_id) == 0


async def test_purge_refcount(owners, sm, store) -> None:  # type: ignore[no-untyped-def]
    o = await owners()
    data = b"%PDF-1.7 shared bytes " + uuid.uuid4().bytes
    a = await seed_document(sm, o.id, content=data, store=store)
    b = await seed_document(sm, o.id, content=data, store=store)  # same blob, refcount 2
    key = f"blobs/{a.sha256}"
    assert await _one(sm, "SELECT refcount FROM blobs WHERE sha256 = :s", s=a.sha256) == 2
    await _mark_deleted(sm, a.document_id)
    await purge_document(sm, store, a.document_id)
    assert await store.exists(key)
    assert await _one(sm, "SELECT refcount FROM blobs WHERE sha256 = :s", s=a.sha256) == 1
    await _mark_deleted(sm, b.document_id)
    await purge_document(sm, store, b.document_id)
    assert not await store.exists(key)
    assert await _one(sm, "SELECT count(*) FROM blobs WHERE sha256 = :s", s=a.sha256) == 0


async def test_abort_mid_pipeline(owners, sm) -> None:  # type: ignore[no-untyped-def]
    from tests.support.jobs import job_row, make_job, version_status

    o = await owners()
    j = await make_job(sm, o.id)

    class DeletingStage:
        """Simulates DELETE arriving while the stage computes, then tries to write pages."""

        name = "extract"

        async def run(self, ctx: StageContext) -> StageResult:
            await _mark_deleted(sm, j.document_id)

            async def write(s: AsyncSession) -> None:
                await s.execute(
                    text(
                        "INSERT INTO pages (version_id, page_no, width, height, text_chars) "
                        "VALUES (:v, 1, 1, 1, 1)"
                    ),
                    {"v": j.version_id},
                )

            return StageResult(write=write)

    enq: list[Any] = []

    async def enqueue(stage: str, job_id: uuid.UUID) -> None:
        enq.append((stage, job_id))

    out = await run_job(
        sm, j.job_id, "w1", {"extract": DeletingStage()}, enqueue, order=("extract", "quality")
    )
    assert out == "deleted"
    assert await _one(sm, "SELECT count(*) FROM pages WHERE version_id = :v", v=j.version_id) == 0
    row = await job_row(sm, j.job_id)
    assert row["status"] == "dead" and row["error_code"] == "deleted" and row["lease_owner"] is None
    assert row["current_stage"] == "extract" and enq == []
    assert await _one(sm, "SELECT count(*) FROM job_events WHERE job_id = :j", j=j.job_id) == 0
    assert (await version_status(sm, j.version_id))[0] == "processing"  # status left alone


async def test_purge_idempotent(owners, sm, store) -> None:  # type: ignore[no-untyped-def]
    o = await owners()
    data = b"%PDF-1.7 idem " + uuid.uuid4().bytes
    a, _ = await _full_doc(sm, store, o.id, content=data)
    other = await seed_document(sm, o.id, content=data, store=store)  # keeps the blob alive (refcount 2)
    # a ledger reference keeps the version row, so the purge marker (not row deletion) must guard the refcount
    run_id = uuid7()
    async with sm() as s:
        await s.execute(
            text(
                "INSERT INTO rag_runs (id, owner_id, route, mode, original_query, status) "
                "VALUES (:r, :o, 'rag', 'chat', 'q', 'done')"
            ),
            {"r": run_id, "o": o.id},
        )
        await s.execute(
            text("INSERT INTO run_versions (run_id, version_id) VALUES (:r, :v)"),
            {"r": run_id, "v": a.version_id},
        )
        await s.commit()
    await _mark_deleted(sm, a.document_id)
    for _ in range(3):
        await purge_document(sm, store, a.document_id)
    assert await _one(sm, "SELECT refcount FROM blobs WHERE sha256 = :s", s=a.sha256) == 1
    assert await store.exists(f"blobs/{a.sha256}")
    assert await _one(sm, "SELECT count(*) FROM document_versions WHERE id = :v", v=a.version_id) == 1
    assert await _one(sm, "SELECT count(*) FROM chunks WHERE version_id = :v", v=a.version_id) == 0
    assert other.sha256 == a.sha256


async def test_cross_owner_404(api: httpx.AsyncClient, owners, sm, purges) -> None:  # type: ignore[no-untyped-def]
    a, b = await owners(), await owners()
    s = await seed_document(sm, a.id)
    r = await api.delete(f"/v1/documents/{s.document_id}", headers=b.auth)
    assert r.status_code == 404
    assert await _one(sm, "SELECT deleted_at FROM documents WHERE id = :d", d=s.document_id) is None
    assert purges.calls == []


async def test_lost_purge_recovered(owners, sm, store) -> None:  # type: ignore[no-untyped-def]
    """The purge enqueue after DELETE is not transactional; the beat sweep purges what it missed."""
    from app.documents.delete import purge_pending

    o = await owners()
    doc, _ = await _full_doc(sm, store, o.id)
    await _mark_deleted(sm, doc.document_id)  # deleted, but no purge ever ran
    assert await purge_pending(sm, store) == 0  # inside the grace period: the normal purge may still come
    async with sm() as s:
        await s.execute(
            text("UPDATE documents SET deleted_at = now() - interval '1 hour' WHERE id = :d"),
            {"d": doc.document_id},
        )
        await s.commit()
    assert await purge_pending(sm, store) >= 1
    assert await _one(sm, "SELECT count(*) FROM chunks WHERE version_id = :v", v=doc.version_id) == 0
    left = await _one(
        sm, "SELECT count(*) FROM document_versions WHERE id = :v AND purged_at IS NULL", v=doc.version_id
    )
    assert left == 0
