"""Document and version creation with per-owner dedupe (steps 4 to 6).

The caller owns the transaction: `create_from_upload` only adds rows and the blob reference, so a failure
anywhere before commit leaves no document, version, job or refcount change behind.
"""

import pathlib
import uuid
from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import uuid7
from app.db.models import Document, DocumentVersion, ProcessingJob
from app.documents.blobs import add_blob_ref
from app.documents.upload import ReceivedFile

FIRST_STAGE = "extract"
MAX_TITLE_CHARS = 300


@dataclass(frozen=True)
class UploadOutcome:
    document_id: uuid.UUID
    version_id: uuid.UUID
    job_id: uuid.UUID | None
    status: str
    deduplicated: bool


def default_title(filename: str) -> str:
    return (pathlib.PurePath(filename).stem or filename)[:MAX_TITLE_CHARS]


async def create_from_upload(
    session: AsyncSession, owner_id: uuid.UUID, f: ReceivedFile, mime: str
) -> UploadOutcome:
    # Serialize identical uploads by the same owner so concurrent requests cannot both create a version.
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": f"upload:{owner_id}:{f.sha256}"}
    )
    existing = (
        await session.execute(
            select(DocumentVersion.id, DocumentVersion.document_id, DocumentVersion.status)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(
                DocumentVersion.owner_id == owner_id,
                DocumentVersion.sha256 == f.sha256,
                Document.deleted_at.is_(None),
            )
            .order_by(DocumentVersion.created_at.desc())
            .limit(1)
        )
    ).first()
    if existing is not None:
        return UploadOutcome(existing.document_id, existing.id, None, existing.status, deduplicated=True)

    await add_blob_ref(session, f.sha256, f.size, mime)
    doc = Document(
        id=uuid7(), owner_id=owner_id, title=(f.title or default_title(f.filename))[:MAX_TITLE_CHARS]
    )
    session.add(doc)
    await session.flush()  # documents before versions (FK)
    return await _add_version(session, owner_id, doc.id, 1, f, mime)


async def _add_version(
    session: AsyncSession,
    owner_id: uuid.UUID,
    document_id: uuid.UUID,
    version_no: int,
    f: ReceivedFile,
    mime: str,
) -> UploadOutcome:
    """The version row (status `uploaded`) and its pending job; the blob reference is already taken."""
    ver = DocumentVersion(
        id=uuid7(),
        document_id=document_id,
        owner_id=owner_id,
        version_no=version_no,
        filename=f.filename,
        mime=mime,
        size_bytes=f.size,
        sha256=f.sha256,
        status="uploaded",
    )
    job = ProcessingJob(
        id=uuid7(), version_id=ver.id, owner_id=owner_id, current_stage=FIRST_STAGE, status="pending"
    )
    session.add(ver)
    await session.flush()  # versions before jobs (FK)
    session.add(job)
    await session.flush()
    return UploadOutcome(document_id, ver.id, job.id, ver.status, deduplicated=False)


async def owns_live_document(session: AsyncSession, owner_id: uuid.UUID, document_id: uuid.UUID) -> bool:
    return (
        await session.execute(
            select(Document.id).where(
                Document.id == document_id, Document.owner_id == owner_id, Document.deleted_at.is_(None)
            )
        )
    ).first() is not None


async def create_new_version(
    session: AsyncSession, owner_id: uuid.UUID, document_id: uuid.UUID, f: ReceivedFile, mime: str
) -> UploadOutcome | None:
    """Add version `max + 1` to the owner's live document (None when it is not theirs or is deleted).

    Same bytes as the document's newest version -> that version, `deduplicated=True`, nothing created. (The
    newest, not only the current one: a client retry while the new version is still processing must not stack
    versions. Bytes of an older version are a revert and become a new version.) The document row lock
    serializes concurrent uploads, so `max + 1` cannot collide.
    """
    locked = (
        await session.execute(
            select(Document.id)
            .where(Document.id == document_id, Document.owner_id == owner_id, Document.deleted_at.is_(None))
            .with_for_update()
        )
    ).first()
    if locked is None:
        return None
    newest = (
        await session.execute(
            select(
                DocumentVersion.id, DocumentVersion.version_no, DocumentVersion.sha256, DocumentVersion.status
            )
            .where(DocumentVersion.document_id == document_id, DocumentVersion.owner_id == owner_id)
            .order_by(DocumentVersion.version_no.desc())
            .limit(1)
        )
    ).first()
    if newest is not None and newest.sha256 == f.sha256:
        return UploadOutcome(document_id, newest.id, None, newest.status, deduplicated=True)
    await add_blob_ref(session, f.sha256, f.size, mime)
    return await _add_version(
        session, owner_id, document_id, (newest.version_no if newest else 0) + 1, f, mime
    )
