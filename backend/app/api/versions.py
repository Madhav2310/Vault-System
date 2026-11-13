"""Version endpoints (design §12): processing timeline, original file, page layout."""

import asyncio
import gzip
import json
import urllib.parse
import uuid
import weakref
from collections.abc import AsyncIterator
from typing import Annotated, Any

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import get_db, get_store
from app.api.errors import DomainError
from app.core.auth import CurrentUser
from app.core.error_codes import ErrorCode
from app.core.settings import get_settings
from app.db.session import get_sessionmaker
from app.documents.blobs import blob_key
from app.ingestion.events import stream_processing
from app.storage.base import ObjectStore

router = APIRouter(prefix="/v1/versions", tags=["versions"])
STREAM_CHUNK = 1024 * 1024


async def _owned_version(db: AsyncSession, owner_id: uuid.UUID, version_id: uuid.UUID) -> Any:
    """The version row, or 404. A version of a deleted document counts as absent."""
    row = (
        (
            await db.execute(
                text(
                    "SELECT v.id, v.status, v.error_code, v.quality, v.filename, v.mime, v.sha256, "
                    "v.size_bytes FROM document_versions v JOIN documents d ON d.id = v.document_id "
                    "WHERE v.id = :v AND v.owner_id = :o AND d.owner_id = :o AND d.deleted_at IS NULL"
                ),
                {"v": version_id, "o": owner_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise DomainError(ErrorCode.NOT_FOUND)
    return row


@router.get("/{version_id}/processing")
async def processing(
    version_id: uuid.UUID, user: CurrentUser, db: Annotated[AsyncSession, Depends(get_db)]
) -> dict[str, Any]:
    """Stage timeline from `job_events` (oldest first), the job's state, per-page flags and quality."""
    v = await _owned_version(db, user.id, version_id)
    job = (
        (
            await db.execute(
                text(
                    "SELECT id, status, current_stage, attempt_count, error_code FROM processing_jobs "
                    "WHERE version_id = :v AND owner_id = :o"
                ),
                {"v": version_id, "o": user.id},
            )
        )
        .mappings()
        .first()
    )
    events = (
        (
            await db.execute(
                text(
                    "SELECT stage, status, attempt, duration_ms, created_at FROM job_events "
                    "WHERE job_id = :j ORDER BY created_at, id"
                ),
                {"j": job["id"] if job else None},
            )
        )
        .mappings()
        .all()
    )
    pages = (
        (
            await db.execute(
                text(
                    "SELECT p.page_no, p.flags, p.quality_score, p.text_chars FROM pages p "
                    "WHERE p.version_id = :v ORDER BY p.page_no"
                ),
                {"v": version_id},
            )
        )
        .mappings()
        .all()
    )
    return {
        "version_id": str(version_id),
        "status": v["status"],
        "error_code": v["error_code"],
        "quality": v["quality"],
        "job": (
            {
                "status": job["status"],
                "current_stage": job["current_stage"],
                "attempt_count": job["attempt_count"],
                "error_code": job["error_code"],
            }
            if job
            else None
        ),
        "stages": [
            {
                "stage": e["stage"],
                "status": e["status"],
                "attempt": e["attempt"],
                "duration_ms": e["duration_ms"],
                "at": e["created_at"].isoformat(),
            }
            for e in events
        ],
        "pages": [
            {
                "page_no": p["page_no"],
                "flags": p["flags"],
                "quality_score": p["quality_score"],
                "text_chars": p["text_chars"],
            }
            for p in pages
        ],
    }


async def _stream(data: bytes) -> AsyncIterator[bytes]:
    for i in range(0, len(data), STREAM_CHUNK):
        yield data[i : i + STREAM_CHUNK]


@router.get("/{version_id}/file")
async def get_file(
    version_id: uuid.UUID,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    store: Annotated[ObjectStore, Depends(get_store)],
) -> StreamingResponse:
    """The original bytes (`blobs/{sha256}`)."""
    v = await _owned_version(db, user.id, version_id)
    await db.rollback()  # release the connection before touching the object store
    try:
        data = await store.get(blob_key(v["sha256"]))
    except FileNotFoundError as e:
        raise DomainError(ErrorCode.NOT_FOUND, "Original file is not available") from e
    quoted = urllib.parse.quote(v["filename"], safe="")
    return StreamingResponse(
        _stream(data),
        media_type=v["mime"],
        headers={
            "Content-Length": str(len(data)),
            "Content-Disposition": f"inline; filename*=UTF-8''{quoted}",
            "ETag": f'"{v["sha256"]}"',
        },
    )


def _page_layout(raw: bytes, page: int) -> dict[str, Any] | None:
    pages = json.loads(gzip.decompress(raw))
    entry = pages.get(str(page))
    return entry if isinstance(entry, dict) else None


@router.get("/{version_id}/pages/{page}/layout")
async def page_layout(
    version_id: uuid.UUID,
    page: int,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    store: Annotated[ObjectStore, Depends(get_store)],
) -> dict[str, Any]:
    """Page size and glyph boxes from `layouts/{version_id}.json.gz` (design §6.4)."""
    await _owned_version(db, user.id, version_id)
    await db.rollback()
    try:
        raw = await store.get(f"layouts/{version_id}.json.gz")
    except FileNotFoundError as e:
        raise DomainError(ErrorCode.NOT_FOUND, "No layout is available for this version") from e
    entry = await asyncio.to_thread(_page_layout, raw, page)
    if entry is None:
        raise DomainError(ErrorCode.NOT_FOUND, f"Page {page} has no layout")
    return {
        "version_id": str(version_id),
        "page": page,
        "width": entry["w"],
        "height": entry["h"],
        "glyphs": entry["glyphs"],
    }


# --- live processing status (S14.05) ----------------------------------------------------------------
_event_clients: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, aioredis.Redis]" = (
    weakref.WeakKeyDictionary()
)


def get_stream_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """The stream reads with short sessions of its own (the request session is not held while waiting)."""
    return get_sessionmaker()


async def get_events_redis() -> aioredis.Redis:
    """Redis for the stream's pub/sub: one client per event loop; no read timeout (pub/sub waits)."""
    loop = asyncio.get_running_loop()
    client = _event_clients.get(loop)
    if client is None:
        client = aioredis.from_url(get_settings().redis_url, socket_connect_timeout=0.5)
        _event_clients[loop] = client
    return client


@router.get(
    "/{version_id}/processing/stream",
    response_class=StreamingResponse,
    responses={
        200: {"content": {"text/event-stream": {}}, "description": "SSE: `stage` events, then `done`"}
    },
)
async def processing_stream(
    version_id: uuid.UUID,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    sm: Annotated[async_sessionmaker[AsyncSession], Depends(get_stream_sessionmaker)],
    redis: Annotated[aioredis.Redis, Depends(get_events_redis)],
) -> StreamingResponse:
    """Server-sent events: one `stage{name, status, attempt, at, error_code?}` per `job_events` row (the past
    first, then live as stages commit), then a terminal `done{status, error_code}`. `: heartbeat` comments
    every 15 s; closes at a terminal status or after 10 minutes (`timeout`: reconnect to resume)."""
    await _owned_version(db, user.id, version_id)
    await db.rollback()  # no connection is held while streaming
    return StreamingResponse(
        stream_processing(sm, redis, user.id, version_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
