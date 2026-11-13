"""Document and version creation with per-owner dedupe (design §6.1, steps 4 to 6).

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
    ver = DocumentVersion(
        id=uuid7(),
        document_id=doc.id,
        owner_id=owner_id,
        version_no=1,
        filename=f.filename,
        mime=mime,
        size_bytes=f.size,
        sha256=f.sha256,
        status="uploaded",
    )
    job = ProcessingJob(
        id=uuid7(), version_id=ver.id, owner_id=owner_id, current_stage=FIRST_STAGE, status="pending"
    )
    session.add(doc)
    await session.flush()  # documents before versions (FK); versions before jobs
    session.add(ver)
    await session.flush()
    session.add(job)
    await session.flush()
    return UploadOutcome(doc.id, ver.id, job.id, ver.status, deduplicated=False)
