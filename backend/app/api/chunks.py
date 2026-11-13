"""Chunk inspection endpoint (design §12): the stored evidence text with its sentences and location."""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.api.errors import DomainError
from app.api.schemas import ChunkOut
from app.core.auth import CurrentUser
from app.core.error_codes import ErrorCode

router = APIRouter(prefix="/v1/chunks", tags=["Documents"])


@router.get(
    "/{chunk_id}",
    summary="Inspect a source chunk",
    response_model=ChunkOut,
    responses={404: {"description": "Not your chunk"}},
)
async def get_chunk(
    chunk_id: uuid.UUID, user: CurrentUser, db: Annotated[AsyncSession, Depends(get_db)]
) -> dict[str, Any]:
    """`text` is `chunks.original_text` (the only citable text); prefixes are never returned."""
    row = (
        (
            await db.execute(
                text(
                    "SELECT c.id, c.document_id, c.ordinal, c.kind, c.original_text, c.sentences, "
                    "c.page_start, c.page_end, c.section_path, c.token_count, v.id AS vid, v.version_no, "
                    "v.filename FROM chunks c "
                    "JOIN document_versions v ON v.id = c.version_id AND v.owner_id = c.owner_id "
                    "JOIN documents d ON d.id = c.document_id AND d.owner_id = c.owner_id "
                    "WHERE c.owner_id = :o AND c.id = :c AND d.deleted_at IS NULL"
                ),
                {"o": user.id, "c": chunk_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise DomainError(ErrorCode.NOT_FOUND)
    return {
        "id": str(row["id"]),
        "document_id": str(row["document_id"]),
        "ordinal": row["ordinal"],
        "kind": row["kind"],
        "text": row["original_text"],
        "sentences": row["sentences"],
        "page_start": row["page_start"],
        "page_end": row["page_end"],
        "section_path": row["section_path"],
        "token_count": row["token_count"],
        "version": {"id": str(row["vid"]), "version_no": row["version_no"], "filename": row["filename"]},
    }
