"""Soft delete and purge.

`DELETE /documents/{id}` only sets `documents.deleted_at` (every read path filters on it) and enqueues a
purge. The purge works per version and is idempotent: derived rows and objects are removed, the blob
refcount is decremented exactly once, and the blob goes when its refcount reaches 0.

Idempotency of the decrement: a purged version either has its row deleted, or, when the permanent ledger
still references it (`run_versions`, `matrix_cells`), keeps its row with `purged_at` set and is skipped
from then on. The marker and the decrement commit in the same transaction.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import get_logger
from app.documents.blobs import blob_key
from app.storage.base import ObjectStore

SessionFactory = async_sessionmaker[AsyncSession]

PURGE_TASK = "vault.maintenance.purge_document"
PurgeEnqueue = Callable[[uuid.UUID], Awaitable[None]]
_log = get_logger("vault.documents.delete")


def get_purge_enqueue() -> PurgeEnqueue:
    """FastAPI dependency: production enqueuer (Celery `send_task` off the event loop); tests override it."""
    from app.worker.celery_app import celery_app

    async def enqueue(document_id: uuid.UUID) -> None:
        await asyncio.to_thread(celery_app.send_task, PURGE_TASK, args=[str(document_id)])

    return enqueue


async def soft_delete(db: AsyncSession, owner_id: uuid.UUID, document_id: uuid.UUID) -> bool:
    """Set `deleted_at` on the owner's live document. False when there is no such document."""
    row = (
        await db.execute(
            text(
                "UPDATE documents SET deleted_at = now() "
                "WHERE id = :d AND owner_id = :o AND deleted_at IS NULL RETURNING id"
            ),
            {"d": document_id, "o": owner_id},
        )
    ).first()
    await db.commit()
    return row is not None


async def purge_document(sm: SessionFactory, store: ObjectStore, document_id: uuid.UUID) -> int:
    """Purge every not-yet-purged version of a soft-deleted document; returns how many were purged."""
    async with sm() as s:
        doc = (
            await s.execute(
                text("SELECT owner_id FROM documents WHERE id = :d AND deleted_at IS NOT NULL"),
                {"d": document_id},
            )
        ).first()
        if doc is None:
            return 0  # unknown, or not deleted: never purge a live document
        owner_id: uuid.UUID = doc[0]
        version_ids = (
            (
                await s.execute(
                    text(
                        "SELECT id FROM document_versions WHERE document_id = :d AND owner_id = :o "
                        "AND purged_at IS NULL ORDER BY version_no"
                    ),
                    {"d": document_id, "o": owner_id},
                )
            )
            .scalars()
            .all()
        )
    purged = 0
    for vid in version_ids:
        if await _purge_version(sm, store, owner_id, document_id, vid):
            purged += 1
    _log.info("document_purged", document_id=str(document_id), versions=purged)
    return purged


async def _purge_version(
    sm: SessionFactory, store: ObjectStore, owner_id: uuid.UUID, document_id: uuid.UUID, version_id: uuid.UUID
) -> bool:
    # Derived objects first: deletes are idempotent, and a crash before the commit just reruns them.
    await store.delete(f"layouts/{version_id}.json.gz")
    await store.delete(f"extracted/{version_id}.json.gz")
    p = {"v": version_id, "o": owner_id, "d": document_id}
    async with sm() as s:
        # Lock order matches the runner's commit (job row, then version, then document): the opposite order
        # between a purge and a stage commit could deadlock.
        await s.execute(
            text("SELECT id FROM processing_jobs WHERE version_id = :v AND owner_id = :o FOR UPDATE"), p
        )
        row = (
            await s.execute(
                text(
                    "SELECT sha256 FROM document_versions WHERE id = :v AND owner_id = :o "
                    "AND purged_at IS NULL FOR UPDATE"
                ),
                p,
            )
        ).first()
        if row is None:
            return False  # a concurrent or earlier purge did it
        sha: str = row[0]
        await s.execute(text("DELETE FROM chunks WHERE owner_id = :o AND version_id = :v"), p)
        await s.execute(text("DELETE FROM pages WHERE version_id = :v"), p)
        await s.execute(text("DELETE FROM document_insights WHERE owner_id = :o AND version_id = :v"), p)
        jobs = "SELECT id FROM processing_jobs WHERE version_id = :v AND owner_id = :o"
        await s.execute(text(f"DELETE FROM job_events WHERE job_id IN ({jobs})"), p)  # noqa: S608
        await s.execute(text(f"UPDATE llm_calls SET job_id = NULL WHERE job_id IN ({jobs})"), p)  # noqa: S608
        await s.execute(text("DELETE FROM processing_jobs WHERE version_id = :v AND owner_id = :o"), p)
        await s.execute(
            text("UPDATE documents SET current_version_id = NULL WHERE id = :d AND current_version_id = :v"),
            p,
        )
        refcount = (
            await s.execute(
                text(
                    "UPDATE blobs SET refcount = GREATEST(refcount - 1, 0) WHERE sha256 = :s "
                    "RETURNING refcount"
                ),
                {"s": sha},
            )
        ).scalar_one_or_none()
        await s.execute(
            text("UPDATE document_versions SET purged_at = now() WHERE id = :v"),
            {"v": version_id},
        )
        # The permanent ledger (runs, matrix cells) may still point at the version: then the row stays.
        await s.execute(
            text(
                "DELETE FROM document_versions WHERE id = :v AND NOT EXISTS "
                "(SELECT 1 FROM run_versions WHERE version_id = :v) AND NOT EXISTS "
                "(SELECT 1 FROM matrix_cells WHERE version_id = :v)"
            ),
            p,
        )
        if refcount == 0:
            await s.execute(
                text(
                    "DELETE FROM blobs WHERE sha256 = :s AND refcount = 0 AND NOT EXISTS "
                    "(SELECT 1 FROM document_versions WHERE sha256 = :s)"
                ),
                {"s": sha},
            )
        await s.commit()
    if refcount == 0:
        await store.delete(blob_key(sha))
    return True


PENDING_GRACE_S = 600


async def purge_pending(sm: SessionFactory, store: ObjectStore, grace_s: int = PENDING_GRACE_S) -> int:
    """Beat safety net: purge documents deleted more than `grace_s` ago that still have unpurged versions
    (the purge enqueue after a DELETE is not transactional and may have been lost)."""
    async with sm() as s:
        ids = (
            (
                await s.execute(
                    text(
                        "SELECT DISTINCT d.id FROM documents d "
                        "JOIN document_versions v ON v.document_id = d.id "
                        "WHERE d.deleted_at < now() - make_interval(secs => :g) AND v.purged_at IS NULL "
                        "LIMIT 200"
                    ),
                    {"g": grace_s},
                )
            )
            .scalars()
            .all()
        )
    for doc_id in ids:
        await purge_document(sm, store, doc_id)
    return len(ids)
