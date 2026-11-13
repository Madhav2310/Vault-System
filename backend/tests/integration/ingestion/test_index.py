"""S1.25 index stage: ready, supersede, staleness hook."""

import pytest
from sqlalchemy import text

from app.ingestion import embed_index
from app.ingestion.embed_index import IndexStage
from app.ingestion.errors import PermanentStageError, RetryableStageError
from app.ingestion.runner import run_job
from tests.support.embed import add_chunks
from tests.support.jobs import make_job, version_status


async def _doc_current(sm, document_id):  # type: ignore[no-untyped-def]
    async with sm() as s:
        return (
            await s.execute(
                text("SELECT current_version_id FROM documents WHERE id = :d"), {"d": document_id}
            )
        ).scalar_one()


async def _new_version(sm, owner_id, document_id, version_no):  # type: ignore[no-untyped-def]
    """A second version of an existing document (with its own blob, job and embedded chunks)."""
    import hashlib
    import uuid

    from app.core.ids import uuid7

    sha, v, j = hashlib.sha256(uuid.uuid4().bytes).hexdigest(), uuid7(), uuid7()
    async with sm() as s:
        await s.execute(
            text(
                "INSERT INTO blobs (sha256, size_bytes, mime, storage_key, refcount) "
                "VALUES (:s, 1, 'text/plain', :k, 1)"
            ),
            {"s": sha, "k": f"blobs/{sha}"},
        )
        await s.execute(
            text(
                "INSERT INTO document_versions (id, document_id, owner_id, version_no, filename, mime, "
                "size_bytes, sha256, status) "
                "VALUES (:v, :d, :o, :n, 'a.txt', 'text/plain', 1, :s, 'processing')"
            ),
            {"v": v, "d": document_id, "o": owner_id, "n": version_no, "s": sha},
        )
        await s.execute(
            text(
                "INSERT INTO processing_jobs (id, version_id, owner_id, current_stage, status) "
                "VALUES (:j, :v, :o, 'index', 'pending')"
            ),
            {"j": j, "v": v, "o": owner_id},
        )
        await s.commit()
    return v, j


async def _run_index(sm, store, enqueued, job_id):  # type: ignore[no-untyped-def]
    from app.core.settings import get_settings

    return await run_job(
        sm,
        job_id,
        "w",
        {"index": IndexStage()},
        enqueued,
        order=("index",),
        services={"store": store, "settings": get_settings()},
    )


async def test_ready(sm, make_owner, store, enqueued) -> None:  # type: ignore[no-untyped-def]
    o = await make_owner()
    ref = await make_job(sm, o.id, stage="index")
    await add_chunks(sm, o.id, ref, 3, embedded=True)
    assert await _run_index(sm, store, enqueued, ref.job_id) == "finished"
    assert (await version_status(sm, ref.version_id))[0] == "ready"
    async with sm() as s:
        ready_at = (
            await s.execute(
                text("SELECT ready_at FROM document_versions WHERE id = :v"), {"v": ref.version_id}
            )
        ).scalar_one()
    assert ready_at is not None and await _doc_current(sm, ref.document_id) == ref.version_id


async def test_supersede(sm, make_owner, store, enqueued) -> None:  # type: ignore[no-untyped-def]
    o = await make_owner()
    ref = await make_job(sm, o.id, stage="index")
    await add_chunks(sm, o.id, ref, 2, embedded=True)
    await _run_index(sm, store, enqueued, ref.job_id)
    v2, j2 = await _new_version(sm, o.id, ref.document_id, 2)
    from tests.support.jobs import JobRef

    await add_chunks(sm, o.id, JobRef(j2, v2, ref.document_id), 2, tag="b", embedded=True)
    assert await _run_index(sm, store, enqueued, j2) == "finished"
    assert (await version_status(sm, ref.version_id))[0] == "superseded"
    assert (await version_status(sm, v2))[0] == "ready"
    assert await _doc_current(sm, ref.document_id) == v2


async def test_failed_v2_keeps_v1(sm, make_owner, store, enqueued) -> None:  # type: ignore[no-untyped-def]
    o = await make_owner()
    ref = await make_job(sm, o.id, stage="index")
    await add_chunks(sm, o.id, ref, 2, embedded=True)
    await _run_index(sm, store, enqueued, ref.job_id)
    v2, j2 = await _new_version(sm, o.id, ref.document_id, 2)
    from tests.support.jobs import JobRef

    await add_chunks(
        sm, o.id, JobRef(j2, v2, ref.document_id), 2, tag="b", embedded=False
    )  # embeddings missing
    assert await _run_index(sm, store, enqueued, j2) == "retry"  # v2 is not ready, v1 untouched
    assert (await version_status(sm, ref.version_id))[0] == "ready"
    assert await _doc_current(sm, ref.document_id) == ref.version_id
    assert (await version_status(sm, v2))[0] == "processing"
    # a version with no text at all fails permanently and also leaves v1 alone
    v3, j3 = await _new_version(sm, o.id, ref.document_id, 3)
    assert await _run_index(sm, store, enqueued, j3) == "dead"
    assert (await version_status(sm, v3)) == ("failed", "no_text")
    assert await _doc_current(sm, ref.document_id) == ref.version_id


async def test_staleness_hook_called(
    sm, make_owner, store, enqueued, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    calls: list[object] = []

    async def hook(session, owner_id, document_id, version_id):  # type: ignore[no-untyped-def]
        calls.append(version_id)

    monkeypatch.setattr(embed_index, "on_version_ready", hook)
    o = await make_owner()
    ref = await make_job(sm, o.id, stage="index")
    await add_chunks(sm, o.id, ref, 2, embedded=True)
    await _run_index(sm, store, enqueued, ref.job_id)
    # a redelivered index stage for the already-ready version must not call the hook again
    async with sm() as s:
        await s.execute(
            text("UPDATE processing_jobs SET current_stage = 'index', status = 'pending' WHERE id = :j"),
            {"j": ref.job_id},
        )
        await s.commit()
    await _run_index(sm, store, enqueued, ref.job_id)
    assert calls == [ref.version_id]
    _ = (PermanentStageError, RetryableStageError)
