"""Create a document + version + job directly (no upload) for job-pipeline tests."""

import hashlib
import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.ids import uuid7


@dataclass(frozen=True)
class JobRef:
    job_id: uuid.UUID
    version_id: uuid.UUID
    document_id: uuid.UUID


async def make_job(
    sm: async_sessionmaker[AsyncSession],
    owner_id: uuid.UUID,
    *,
    stage: str = "extract",
    status: str = "pending",
) -> JobRef:
    sha = hashlib.sha256(uuid.uuid4().bytes).hexdigest()
    d, v, j = uuid7(), uuid7(), uuid7()
    async with sm() as s:
        await s.execute(
            text(
                "INSERT INTO blobs (sha256, size_bytes, mime, storage_key, refcount) "
                "VALUES (:s, 1, 'text/plain', :k, 1)"
            ),
            {"s": sha, "k": f"blobs/{sha}"},
        )
        await s.execute(
            text("INSERT INTO documents (id, owner_id, title) VALUES (:d, :o, 't')"), {"d": d, "o": owner_id}
        )
        await s.execute(
            text(
                "INSERT INTO document_versions (id, document_id, owner_id, version_no, filename, mime, "
                "size_bytes, sha256, status) VALUES (:v, :d, :o, 1, 'a.txt', 'text/plain', 1, :s, 'uploaded')"
            ),
            {"v": v, "d": d, "o": owner_id, "s": sha},
        )
        await s.execute(
            text(
                "INSERT INTO processing_jobs (id, version_id, owner_id, current_stage, status) "
                "VALUES (:j, :v, :o, :st, :status)"
            ),
            {"j": j, "v": v, "o": owner_id, "st": stage, "status": status},
        )
        await s.commit()
    return JobRef(j, v, d)


async def job_row(sm: async_sessionmaker[AsyncSession], job_id: uuid.UUID) -> dict[str, object]:
    async with sm() as s:
        r = (
            (await s.execute(text("SELECT * FROM processing_jobs WHERE id = :j"), {"j": job_id}))
            .mappings()
            .one()
        )
        return dict(r)


async def version_status(
    sm: async_sessionmaker[AsyncSession], version_id: uuid.UUID
) -> tuple[str, str | None]:
    async with sm() as s:
        r = (
            await s.execute(
                text("SELECT status, error_code FROM document_versions WHERE id = :v"), {"v": version_id}
            )
        ).one()
        return r[0], r[1]


async def events(sm: async_sessionmaker[AsyncSession], job_id: uuid.UUID) -> list[tuple[str, str, int]]:
    async with sm() as s:
        rows = await s.execute(
            text("SELECT stage, status, attempt FROM job_events WHERE job_id = :j ORDER BY created_at, id"),
            {"j": job_id},
        )
        return [tuple(r) for r in rows]  # type: ignore[misc]


async def expire_lease(sm: async_sessionmaker[AsyncSession], job_id: uuid.UUID) -> None:
    async with sm() as s:
        await s.execute(
            text("UPDATE processing_jobs SET lease_expires_at = now() - interval '1 second' WHERE id = :j"),
            {"j": job_id},
        )
        await s.commit()
