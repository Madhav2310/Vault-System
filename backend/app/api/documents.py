"""Document endpoints (design §12). Upload: §6.1."""

import asyncio
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_enqueue, get_store
from app.api.idempotency import canonical_response, check_key, request_hash, run_idempotent
from app.core.auth import CurrentUser
from app.core.settings import get_settings
from app.documents.blobs import store_blob
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
    if status == 202 and body.get("job_id"):
        # After commit (§6.1 step 7). CAS-guarded, so an idempotent replay cannot enqueue twice; if this
        # enqueue is lost, the sweeper re-dispatches after the grace period.
        await dispatch(db, uuid.UUID(body["job_id"]), enqueue)
    return canonical_response(status, body)
