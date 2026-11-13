"""Seed documents, versions, pages, chunks and insights directly (no upload) for read/delete tests."""

import datetime as dt
import gzip
import hashlib
import json
import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.ids import uuid7
from app.storage.base import ObjectStore


@dataclass(frozen=True)
class Seeded:
    document_id: uuid.UUID
    version_id: uuid.UUID
    sha256: str
    job_id: uuid.UUID


async def seed_document(
    sm: async_sessionmaker[AsyncSession],
    owner_id: uuid.UUID,
    *,
    title: str = "Doc",
    status: str = "ready",
    created_at: dt.datetime | None = None,
    content: bytes | None = None,
    tags: list[str] | None = None,
    category: str | None = None,
    quality: dict[str, object] | None = None,
    store: ObjectStore | None = None,
    mime: str = "application/pdf",
) -> Seeded:
    data = content if content is not None else uuid.uuid4().bytes
    sha = hashlib.sha256(data).hexdigest()
    if store is not None:
        await store.put(f"blobs/{sha}", data)
    d, v, j = uuid7(), uuid7(), uuid7()
    async with sm() as s:
        await s.execute(
            text(
                "INSERT INTO blobs (sha256, size_bytes, mime, storage_key, refcount) "
                "VALUES (:s, :n, :m, :k, 1) ON CONFLICT (sha256) DO UPDATE SET refcount = blobs.refcount + 1"
            ),
            {"s": sha, "n": len(data), "m": mime, "k": f"blobs/{sha}"},
        )
        await s.execute(
            text(
                "INSERT INTO documents (id, owner_id, title, tags, category, created_at) "
                "VALUES (:d, :o, :t, :tags, :c, COALESCE(:ca, now()))"
            ),
            {"d": d, "o": owner_id, "t": title, "tags": tags or [], "c": category, "ca": created_at},
        )
        await s.execute(
            text(
                "INSERT INTO document_versions (id, document_id, owner_id, version_no, filename, mime, "
                "size_bytes, sha256, status, quality) VALUES (:v, :d, :o, 1, 'a.pdf', :m, :n, :s, :st, "
                "CAST(:q AS jsonb))"
            ),
            {
                "v": v,
                "d": d,
                "o": owner_id,
                "m": mime,
                "n": len(data),
                "s": sha,
                "st": status,
                "q": json.dumps(quality) if quality is not None else None,
            },
        )
        if status in ("ready", "enriched"):
            await s.execute(
                text("UPDATE documents SET current_version_id = :v WHERE id = :d"), {"v": v, "d": d}
            )
        await s.execute(
            text(
                "INSERT INTO processing_jobs (id, version_id, owner_id, current_stage, status) "
                "VALUES (:j, :v, :o, 'extract', 'pending')"
            ),
            {"j": j, "v": v, "o": owner_id},
        )
        await s.commit()
    return Seeded(d, v, sha, j)


async def seed_chunk(
    sm: async_sessionmaker[AsyncSession],
    owner_id: uuid.UUID,
    seeded: Seeded,
    *,
    ordinal: int = 0,
    original_text: str = "Alpha beta. Gamma delta.",
    page_start: int = 1,
    page_end: int = 1,
    section_path: list[str] | None = None,
) -> uuid.UUID:
    cid = uuid7()
    sentences = [
        {"i": 0, "text": "Alpha beta.", "start": 0, "end": 11},
        {"i": 1, "text": "Gamma delta.", "start": 12, "end": 24},
    ]
    async with sm() as s:
        await s.execute(
            text(
                "INSERT INTO chunks (id, owner_id, document_id, version_id, ordinal, kind, original_text, "
                "embedding_text, page_start, page_end, section_path, sentences, spans, token_count, "
                "content_hash) VALUES (:id, :o, :d, :v, :ord, 'text', :t, :t, :ps, :pe, :sp, "
                "CAST(:sent AS jsonb), CAST('[]' AS jsonb), 5, 'h')"
            ),
            {
                "id": cid,
                "o": owner_id,
                "d": seeded.document_id,
                "v": seeded.version_id,
                "ord": ordinal,
                "t": original_text,
                "ps": page_start,
                "pe": page_end,
                "sp": section_path or [],
                "sent": json.dumps(sentences),
            },
        )
        await s.commit()
    return cid


async def seed_pages(
    sm: async_sessionmaker[AsyncSession], version_id: uuid.UUID, flags: dict[int, list[str]]
) -> None:
    async with sm() as s:
        for page_no, fl in flags.items():
            await s.execute(
                text(
                    "INSERT INTO pages (version_id, page_no, width, height, text_chars, flags) "
                    "VALUES (:v, :p, 612, 792, 10, :f)"
                ),
                {"v": version_id, "p": page_no, "f": fl},
            )
        await s.commit()


async def seed_insight(
    sm: async_sessionmaker[AsyncSession], owner_id: uuid.UUID, version_id: uuid.UUID
) -> None:
    async with sm() as s:
        await s.execute(
            text(
                "INSERT INTO document_insights (id, owner_id, version_id, kind, params_hash, status, "
                "prompt_version) VALUES (:i, :o, :v, 'brief', 'h', 'done', 1)"
            ),
            {"i": uuid7(), "o": owner_id, "v": version_id},
        )
        await s.commit()


async def put_gz_json(store: ObjectStore, key: str, obj: object) -> None:
    await store.put(key, gzip.compress(json.dumps(obj).encode()))


async def clear_owner_rows(sm: async_sessionmaker[AsyncSession], owner_ids: list[uuid.UUID]) -> None:
    """Delete derived rows that `make_owner`'s teardown does not know about (run before it)."""
    async with sm() as s:
        await s.execute(
            text(
                "DELETE FROM run_versions WHERE version_id IN "
                "(SELECT id FROM document_versions WHERE owner_id = ANY(:ids))"
            ),
            {"ids": owner_ids},
        )
        await s.execute(text("DELETE FROM rag_runs WHERE owner_id = ANY(:ids)"), {"ids": owner_ids})
        for tbl in ("chunks", "document_insights"):
            await s.execute(text(f"DELETE FROM {tbl} WHERE owner_id = ANY(:ids)"), {"ids": owner_ids})
        await s.execute(
            text(
                "DELETE FROM pages WHERE version_id IN "
                "(SELECT id FROM document_versions WHERE owner_id = ANY(:ids))"
            ),
            {"ids": owner_ids},
        )
        await s.commit()
