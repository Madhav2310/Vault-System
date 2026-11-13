"""S2.01 scope computation against the real schema."""

import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.ids import uuid7
from app.retrieval.scope import compute_scope
from tests.support.docs import seed_document


async def _add_version(
    sm: async_sessionmaker[AsyncSession], owner_id: uuid.UUID, document_id: uuid.UUID, status: str, no: int
) -> uuid.UUID:
    vid = uuid7()
    async with sm() as s:
        await s.execute(
            text(
                "INSERT INTO document_versions (id, document_id, owner_id, version_no, filename, mime, "
                "size_bytes, sha256, status) SELECT :v, :d, :o, :n, filename, mime, size_bytes, sha256, :st "
                "FROM document_versions WHERE document_id = :d AND version_no = 1"
            ),
            {"v": vid, "d": document_id, "o": owner_id, "n": no, "st": status},
        )
        await s.commit()
    return vid


async def _set_current(sm: async_sessionmaker[AsyncSession], document_id: uuid.UUID, vid: uuid.UUID) -> None:
    async with sm() as s:
        await s.execute(
            text("UPDATE documents SET current_version_id = :v WHERE id = :d"), {"v": vid, "d": document_id}
        )
        await s.commit()


async def test_latest_ready_only(sm: async_sessionmaker[AsyncSession], make_owner: Callable[[], Any]) -> None:
    o = await make_owner()
    doc = await seed_document(sm, o.id)
    v2 = await _add_version(sm, o.id, doc.document_id, "enriched", 2)
    await _set_current(sm, doc.document_id, v2)
    async with sm() as s:
        scope = await compute_scope(s, o.id, None)
    assert scope.version_ids == (v2,)
    assert doc.version_id not in scope.version_ids
    assert scope.document_ids == (doc.document_id,)


async def test_excludes_non_ready(
    sm: async_sessionmaker[AsyncSession], make_owner: Callable[[], Any]
) -> None:
    o = await make_owner()
    ready = await seed_document(sm, o.id)
    for st in ("processing", "failed", "needs_ocr"):
        await seed_document(sm, o.id, status=st)
    deleted = await seed_document(sm, o.id)
    async with sm() as s:
        await s.execute(
            text("UPDATE documents SET deleted_at = now() WHERE id = :d"), {"d": deleted.document_id}
        )
        await s.commit()
    # a document whose current version is mid-reprocess (not ready) is out even though v1 was ready
    reproc = await seed_document(sm, o.id)
    v2 = await _add_version(sm, o.id, reproc.document_id, "processing", 2)
    await _set_current(sm, reproc.document_id, v2)
    async with sm() as s:
        scope = await compute_scope(s, o.id, None)
        assert scope.version_ids == (ready.version_id,)
        # explicit ids that are not live are excluded and reported
        wanted = [ready.document_id, deleted.document_id, reproc.document_id]
        scope2 = await compute_scope(s, o.id, wanted)
    assert scope2.version_ids == (ready.version_id,)
    assert set(scope2.excluded_document_ids) == {deleted.document_id, reproc.document_id}


async def test_foreign_ids_ignored(
    sm: async_sessionmaker[AsyncSession], make_owner: Callable[[], Any]
) -> None:
    a, b = await make_owner(), await make_owner()
    mine = await seed_document(sm, a.id)
    theirs = await seed_document(sm, b.id)
    async with sm() as s:
        scope = await compute_scope(s, a.id, [mine.document_id, theirs.document_id])
        only_foreign = await compute_scope(s, a.id, [theirs.document_id])
        whole = await compute_scope(s, a.id, [])
    assert scope.version_ids == (mine.version_id,)
    assert scope.excluded_document_ids == (theirs.document_id,)
    assert only_foreign.version_ids == ()
    assert only_foreign.reason == "SCOPE_EMPTY"
    assert whole.version_ids == (mine.version_id,)  # empty list = whole vault, still owner-only
