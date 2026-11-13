"""Document endpoints (design §12). Upload: §6.1."""

import asyncio
import base64
import datetime as dt
import json
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_enqueue, get_store
from app.api.errors import DomainError
from app.api.idempotency import canonical_response, check_key, request_hash, run_idempotent
from app.core.auth import CurrentUser
from app.core.error_codes import ErrorCode
from app.core.settings import get_settings
from app.documents.blobs import store_blob
from app.documents.delete import PurgeEnqueue, get_purge_enqueue, soft_delete
from app.documents.service import create_from_upload
from app.documents.sniff import sniff
from app.documents.upload import receive_upload
from app.ingestion.jobs import Enqueue, dispatch
from app.storage.base import ObjectStore

router = APIRouter(prefix="/v1", tags=["documents"])


def _body(document_id: Any, version_id: Any, job_id: Any, status: str, deduplicated: bool) -> dict[str, Any]:
    return {
        "document_id": str(document_id),
        "version_id": str(version_id),
        "job_id": str(job_id) if job_id else None,
        "status": status,
        "deduplicated": deduplicated,
        "links": {
            "document": f"/v1/documents/{document_id}",
            "processing": f"/v1/versions/{version_id}/processing",
        },
    }


@router.post(
    "/documents",
    status_code=202,
    responses={200: {"description": "Identical bytes already uploaded by this owner (deduplicated)"}},
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "required": ["file"],
                        "properties": {
                            "file": {"type": "string", "format": "binary"},
                            "title": {"type": "string", "maxLength": 300},
                        },
                    }
                }
            },
        }
    },
)
async def upload_document(
    request: Request,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    store: Annotated[ObjectStore, Depends(get_store)],
    enqueue: Annotated[Enqueue, Depends(get_enqueue)],
    idempotency_key: Annotated[str | None, Header()] = None,
) -> Response:
    """Upload a PDF, DOCX, TXT or MD file (≤ 25 MB). Returns 202 and starts processing; re-uploading the same
    bytes returns the existing version with `deduplicated: true` (200)."""
    key = check_key(idempotency_key)
    settings = get_settings()
    async with receive_upload(request, settings.max_upload_bytes) as f:
        mime = await asyncio.to_thread(sniff, f.path, f.filename)  # reads up to the whole file
        await store_blob(store, f.path, f.sha256)

        async def op() -> tuple[int, dict[str, Any]]:
            out = await create_from_upload(db, user.id, f, mime)
            body = _body(out.document_id, out.version_id, out.job_id, out.status, out.deduplicated)
            return (200 if out.deduplicated else 202), body

        h = request_hash(route="POST /v1/documents", sha256=f.sha256, filename=f.filename, title=f.title)
        status, body = await run_idempotent(db, user.id, key, h, op)
        if status == 202:
            # Re-assert the blob file after the commit: a purge of the same bytes (delete, then re-upload) may
            # have removed it between our write and the commit. `put_file` is a no-op when the file is intact.
            await store_blob(store, f.path, f.sha256)
    if status == 202 and body.get("job_id"):
        # After commit (§6.1 step 7). CAS-guarded, so an idempotent replay cannot enqueue twice; if this
        # enqueue is lost, the sweeper re-dispatches after the grace period.
        await dispatch(db, uuid.UUID(body["job_id"]), enqueue)
    return canonical_response(status, body)


# --- read endpoints (S1.27) ---------------------------------------------------------------------------------
VersionStatus = Literal[
    "uploaded", "processing", "ready", "enriched", "needs_ocr", "unsupported", "failed", "superseded"
]
DEFAULT_LIMIT, MAX_LIMIT = 20, 100


def _encode_cursor(created_at: dt.datetime, doc_id: uuid.UUID) -> str:
    raw = json.dumps([created_at.isoformat(), str(doc_id)])
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[dt.datetime, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        ts, did = json.loads(raw)
        return dt.datetime.fromisoformat(ts), uuid.UUID(did)
    except (ValueError, TypeError, UnicodeDecodeError) as e:
        raise DomainError(ErrorCode.VALIDATION_ERROR, "Invalid cursor") from e


def _iso(v: dt.datetime | None) -> str | None:
    return v.isoformat() if v else None


def _version_json(r: Any) -> dict[str, Any]:
    return {
        "id": str(r["vid"]),
        "version_no": r["version_no"],
        "filename": r["filename"],
        "mime": r["vmime"],
        "size_bytes": r["size_bytes"],
        "sha256": r["sha256"],
        "page_count": r["page_count"],
        "status": r["status"],
        "quality": r["quality"],
        "error_code": r["error_code"],
        "created_at": _iso(r["vcreated_at"]),
        "ready_at": _iso(r["ready_at"]),
    }


# The status of a document is its current version's (set when a version becomes ready), else its newest.
_DOC_VERSION = (
    "FROM documents d JOIN document_versions v ON v.id = COALESCE(d.current_version_id, "
    "(SELECT id FROM document_versions x WHERE x.document_id = d.id ORDER BY x.version_no DESC LIMIT 1)) "
)
_VERSION_COLS = (
    "v.id AS vid, v.version_no, v.filename, v.mime AS vmime, v.size_bytes, v.sha256, v.page_count, "
    "v.status, v.quality, v.error_code, v.created_at AS vcreated_at, v.ready_at"
)


@router.get("/documents")
async def list_documents(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    status: VersionStatus | None = None,
    q: Annotated[str | None, Query(max_length=300)] = None,
    tag: Annotated[str | None, Query(max_length=100)] = None,
    category: Annotated[str | None, Query(max_length=100)] = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> dict[str, Any]:
    """Newest first, keyset-paginated on (created_at, id). `status` is the current version's status."""
    where = ["d.owner_id = :owner", "d.deleted_at IS NULL"]
    params: dict[str, Any] = {"owner": user.id, "lim": limit + 1}
    if status:
        where.append("v.status = :status")
        params["status"] = status
    if q:
        where.append("strpos(lower(d.title), lower(:q)) > 0")  # substring, no LIKE-escaping needed
        params["q"] = q
    if tag:
        where.append(":tag = ANY(d.tags)")
        params["tag"] = tag
    if category:
        where.append("d.category = :category")
        params["category"] = category
    if cursor:
        params["c_at"], params["c_id"] = _decode_cursor(cursor)
        where.append("(d.created_at, d.id) < (:c_at, :c_id)")
    sql = (
        f"SELECT d.id, d.title, d.category, d.tags, d.created_at, d.current_version_id, {_VERSION_COLS} "
        f"{_DOC_VERSION} WHERE {' AND '.join(where)} ORDER BY d.created_at DESC, d.id DESC LIMIT :lim"
    )
    rows = (await db.execute(text(sql), params)).mappings().all()
    page, more = rows[:limit], len(rows) > limit
    items = [
        {
            "id": str(r["id"]),
            "title": r["title"],
            "status": r["status"],
            "category": r["category"],
            "tags": r["tags"],
            "created_at": _iso(r["created_at"]),
            "version": _version_json(r),
        }
        for r in page
    ]
    nxt = _encode_cursor(page[-1]["created_at"], page[-1]["id"]) if more else None
    return {"items": items, "next_cursor": nxt}


@router.delete("/documents/{document_id}", status_code=204)
async def delete_document(
    document_id: uuid.UUID,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    enqueue_purge: Annotated[PurgeEnqueue, Depends(get_purge_enqueue)],
) -> Response:
    """Soft delete: hidden from every read immediately; chunks, insights, layouts and the blob go in an
    async purge."""
    if not await soft_delete(db, user.id, document_id):
        raise DomainError(ErrorCode.DOCUMENT_NOT_FOUND)
    await enqueue_purge(document_id)
    return Response(status_code=204)


@router.get("/documents/{document_id}")
async def get_document(
    document_id: uuid.UUID, user: CurrentUser, db: Annotated[AsyncSession, Depends(get_db)]
) -> dict[str, Any]:
    doc = (
        (
            await db.execute(
                text(
                    "SELECT id, title, category, tags, created_at, current_version_id FROM documents "
                    "WHERE id = :d AND owner_id = :o AND deleted_at IS NULL"
                ),
                {"d": document_id, "o": user.id},
            )
        )
        .mappings()
        .first()
    )
    if doc is None:
        raise DomainError(ErrorCode.DOCUMENT_NOT_FOUND)
    rows = (
        (
            await db.execute(
                text(
                    f"SELECT {_VERSION_COLS} FROM document_versions v "  # noqa: S608 (constant columns)
                    "WHERE v.document_id = :d AND v.owner_id = :o ORDER BY v.version_no DESC"
                ),
                {"d": document_id, "o": user.id},
            )
        )
        .mappings()
        .all()
    )
    versions = [_version_json(r) for r in rows]
    current = next((v for v in versions if v["id"] == str(doc["current_version_id"])), None) or (
        versions[0] if versions else None
    )
    return {
        "id": str(doc["id"]),
        "title": doc["title"],
        "category": doc["category"],
        "tags": doc["tags"],
        "created_at": _iso(doc["created_at"]),
        "status": current["status"] if current else None,
        "current_version_id": str(doc["current_version_id"]) if doc["current_version_id"] else None,
        "quality": current["quality"] if current else None,
        "versions": versions,
    }
