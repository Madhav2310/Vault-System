"""Load retrieved chunks as `EvidenceChunk`s (stored text and sentence offsets, never altered)."""

import uuid
from collections.abc import Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.evidence import EvidenceChunk


async def load_evidence_chunks(
    session: AsyncSession, owner_id: uuid.UUID, chunk_ids: Sequence[uuid.UUID]
) -> list[EvidenceChunk]:
    """The owner's live chunks among `chunk_ids`, in the given order; unknown/foreign/deleted ids drop."""
    if not chunk_ids:
        return []
    rows = await session.execute(
        text(
            "SELECT c.id, c.version_id, d.title, v.version_no, c.page_start, c.page_end, c.section_path, "
            "c.original_text, c.sentences, c.document_id FROM chunks c "
            "JOIN document_versions v ON v.id = c.version_id AND v.owner_id = c.owner_id "
            "JOIN documents d ON d.id = c.document_id AND d.owner_id = c.owner_id AND d.deleted_at IS NULL "
            "WHERE c.owner_id = :o AND c.id = ANY(:ids)"
        ),
        {"o": owner_id, "ids": list(chunk_ids)},
    )
    by_id = {
        r[0]: EvidenceChunk(
            r[0],
            r[1],
            r[2],
            r[3],
            r[4],
            r[5],
            tuple(r[6] or ()),
            r[7],
            _sentences(r[8]),
            r[9],
        )
        for r in rows
    }
    return [by_id[i] for i in chunk_ids if i in by_id]


def _sentences(raw: Sequence[object]) -> tuple[tuple[int, int], ...]:
    """Stored as `[start, end]` pairs (chunk stage); tolerate `{"start", "end"}` objects."""
    out: list[tuple[int, int]] = []
    for item in raw:
        if isinstance(item, dict):
            out.append((int(item["start"]), int(item["end"])))
        else:
            pair: Sequence[int] = item  # type: ignore[assignment]
            out.append((int(pair[0]), int(pair[1])))
    return tuple(out)
