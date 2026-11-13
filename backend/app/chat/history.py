"""Message history read model (design 12): chronological messages with segments and evidence, owner-scoped."""

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.sessions import DEFAULT_LIMIT, decode_cursor, encode_cursor, get_session


async def list_messages(
    db: AsyncSession,
    owner_id: uuid.UUID,
    session_id: uuid.UUID,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """One page, oldest first within the page. Pages walk backwards from the newest message: `next_cursor`
    (keyset on created_at, id of the page's oldest message) fetches the next older page.
    Raises SessionNotFound (also for another owner's session) and ValueError (bad cursor)."""
    await get_session(db, owner_id, session_id)
    c_at: dt.datetime | None = None
    c_id: uuid.UUID | None = None
    if cursor:
        c_at, c_id = decode_cursor(cursor)
    rows = (
        (
            await db.execute(
                text(
                    "SELECT id, role, content, response_type, coverage_state, run_id, created_at "
                    "FROM chat_messages WHERE session_id = :s AND owner_id = :o "
                    "AND (CAST(:c_at AS timestamptz) IS NULL "
                    "OR (created_at, id) < (CAST(:c_at AS timestamptz), CAST(:c_id AS uuid))) "
                    "ORDER BY created_at DESC, id DESC LIMIT :n"
                ),
                {"s": session_id, "o": owner_id, "c_at": c_at, "c_id": c_id, "n": limit + 1},
            )
        )
        .mappings()
        .all()
    )
    page = list(rows[:limit])
    nxt = encode_cursor(page[-1]["created_at"], page[-1]["id"]) if len(rows) > limit else None
    page.reverse()
    segments = await _segments(db, owner_id, [m["id"] for m in page if m["role"] == "assistant"])
    items = [
        {
            "id": str(m["id"]),
            "role": m["role"],
            "content": m["content"],
            "created_at": m["created_at"].isoformat(),
            "response_type": m["response_type"],
            "coverage_state": m["coverage_state"],
            "staleness": None,
            "run_id": str(m["run_id"]) if m["run_id"] else None,
            "segments": segments.get(m["id"], []),
        }
        for m in page
    ]
    return items, nxt


async def _segments(
    db: AsyncSession, owner_id: uuid.UUID, message_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[dict[str, Any]]]:
    if not message_ids:
        return {}
    rows = (
        (
            await db.execute(
                text(
                    "SELECT s.message_id, s.id AS segment_id, s.ordinal, s.text, s.support, "
                    "e.chunk_id, e.sentence_start, e.sentence_end, e.cited_text, "
                    "c.page_start, c.page_end, c.section_path, c.version_id, v.version_no, "
                    "d.id AS document_id, d.title "
                    "FROM answer_segments s "
                    "JOIN chat_messages m ON m.id = s.message_id AND m.owner_id = :o "
                    "LEFT JOIN segment_evidence e ON e.segment_id = s.id "
                    "LEFT JOIN chunks c ON c.owner_id = :o AND c.id = e.chunk_id "
                    "LEFT JOIN document_versions v ON v.id = c.version_id AND v.owner_id = :o "
                    "LEFT JOIN documents d ON d.id = v.document_id AND d.owner_id = :o "
                    "WHERE s.message_id = ANY(:ids) "
                    "ORDER BY s.message_id, s.ordinal, e.chunk_id, e.sentence_start"
                ),
                {"o": owner_id, "ids": message_ids},
            )
        )
        .mappings()
        .all()
    )
    out: dict[uuid.UUID, list[dict[str, Any]]] = {}
    by_segment: dict[uuid.UUID, dict[str, Any]] = {}
    for r in rows:
        seg = by_segment.get(r["segment_id"])
        if seg is None:
            seg = {"ordinal": r["ordinal"], "text": r["text"], "support": r["support"], "evidence": []}
            by_segment[r["segment_id"]] = seg
            out.setdefault(r["message_id"], []).append(seg)
        if r["chunk_id"] is not None:
            seg["evidence"].append(
                {
                    "chunk_id": str(r["chunk_id"]),
                    "document_id": str(r["document_id"]) if r["document_id"] else None,
                    "document_title": r["title"],
                    "version_id": str(r["version_id"]) if r["version_id"] else None,
                    "version_no": r["version_no"],
                    "pages": [r["page_start"], r["page_end"]] if r["page_start"] is not None else None,
                    "section_path": list(r["section_path"]) if r["section_path"] is not None else None,
                    "sentence_start": r["sentence_start"],
                    "sentence_end": r["sentence_end"],
                    "cited_text": r["cited_text"],
                }
            )
    return out
