"""Chunk inspection endpoint: the stored evidence text with its sentences and location."""

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
    """`text` is `chunks.original_text` (the only citable text); prefixes are never returned.

    `prev_chunk_id` / `next_chunk_id` are the neighbours by ordinal within the same document version
    (`null` at the ends).
    """
    row = (
        (
            await db.execute(
                text(
                    "SELECT c.id, c.document_id, c.ordinal, c.kind, c.original_text, c.sentences, "
                    "c.page_start, c.page_end, c.section_path, c.token_count, v.id AS vid, v.version_no, "
                    "v.filename, "
                    "(SELECT p.id FROM chunks p WHERE p.owner_id = c.owner_id "
                    "AND p.version_id = c.version_id AND p.ordinal < c.ordinal "
                    "ORDER BY p.ordinal DESC LIMIT 1) AS prev_id, "
                    "(SELECT n.id FROM chunks n WHERE n.owner_id = c.owner_id "
                    "AND n.version_id = c.version_id AND n.ordinal > c.ordinal "
                    "ORDER BY n.ordinal ASC LIMIT 1) AS next_id "
                    "FROM chunks c "
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
        "prev_chunk_id": str(row["prev_id"]) if row["prev_id"] else None,
        "next_chunk_id": str(row["next_id"]) if row["next_id"] else None,
        "version": {"id": str(row["vid"]), "version_no": row["version_no"], "filename": row["filename"]},
    }
