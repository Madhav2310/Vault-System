"""Message history read model (design 12): chronological messages with segments and evidence, owner-scoped."""

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.conflicts import ConflictView, load_conflicts
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
                    "SELECT id, role, content, response_type, coverage_state, run_id, created_at, followups, "
                    "stale_since "
                    "FROM chat_messages WHERE session_id = :s AND owner_id = :o "
                    "AND (run_id IS NULL OR run_id NOT IN (SELECT CAST(r.analysis->>'supersedes' AS uuid) "
                    "FROM rag_runs r WHERE r.session_id = :s AND r.owner_id = :o AND r.status = 'completed' "
                    "AND r.analysis->>'supersedes' IS NOT NULL)) "
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
    stale = await _staleness(db, owner_id, page)
    conflicts = await load_conflicts(
        db, owner_id, [m["run_id"] for m in page if m["run_id"] and m["role"] == "assistant"]
    )
    clarify = await _clarify_options(db, owner_id, page)
    return [
        _item(
            m, segments.get(m["id"], []), stale.get(m["id"]), conflicts.get(m["run_id"]), clarify.get(m["id"])
        )
        for m in page
    ], nxt


def _item(
    m: Any,
    segments: list[dict[str, Any]],
    staleness: dict[str, Any] | None = None,
    conflicts: list[ConflictView] | None = None,
    clarify: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    return {
        "id": str(m["id"]),
        "role": m["role"],
        "content": m["content"],
        "created_at": m["created_at"].isoformat(),
        "response_type": m["response_type"],
        "coverage_state": m["coverage_state"],
        "staleness": staleness,
        "run_id": str(m["run_id"]) if m["run_id"] else None,
        "segments": segments,
        "followups": list(m["followups"] or []),
        "conflicts": [c.model_dump() for c in conflicts or []] if m["role"] == "assistant" else [],
        "clarify_options": clarify or [],
    }


async def get_message(db: AsyncSession, owner_id: uuid.UUID, message_id: uuid.UUID) -> dict[str, Any] | None:
    """One message in the history item shape (assistant messages with segments), owner-scoped."""
    m = (
        (
            await db.execute(
                text(
                    "SELECT id, role, content, response_type, coverage_state, run_id, created_at, followups, "
                    "stale_since "
                    "FROM chat_messages WHERE id = :i AND owner_id = :o"
                ),
                {"i": message_id, "o": owner_id},
            )
        )
        .mappings()
        .first()
    )
    if m is None:
        return None
    segments = await _segments(db, owner_id, [m["id"]]) if m["role"] == "assistant" else {}
    stale = await _staleness(db, owner_id, [m])
    conflicts = await load_conflicts(
        db, owner_id, [m["run_id"]] if m["run_id"] and m["role"] == "assistant" else []
    )
    clarify = await _clarify_options(db, owner_id, [m])
    return _item(
        m, segments.get(m["id"], []), stale.get(m["id"]), conflicts.get(m["run_id"]), clarify.get(m["id"])
    )


async def _clarify_options(
    db: AsyncSession, owner_id: uuid.UUID, msgs: list[Any]
) -> dict[uuid.UUID, list[dict[str, str]]]:
    """`{label, document_id}` options of the clarification messages (stored with the run, S23.02)."""
    marked = {m["run_id"]: m["id"] for m in msgs if m["response_type"] == "clarify" and m["run_id"]}
    if not marked:
        return {}
    rows = await db.execute(
        text("SELECT id, analysis->'clarify'->'options' FROM rag_runs WHERE owner_id = :o AND id = ANY(:r)"),
        {"o": owner_id, "r": list(marked)},
    )
    return {marked[r[0]]: list(r[1] or []) for r in rows}


async def clarify_pick(
    db: AsyncSession, owner_id: uuid.UUID, session_id: uuid.UUID, message_id: uuid.UUID, option: int
) -> tuple[str, uuid.UUID] | None:
    """(the clarified question, the chosen document id) for option `option` of a clarification message of this
    session; None when it is not a stored clarification of the owner or the index is out of range."""
    row = (
        await db.execute(
            text(
                "SELECT r.original_query, r.analysis->'clarify'->'options' "
                "FROM chat_messages m JOIN rag_runs r "
                "ON r.id = m.run_id AND r.owner_id = m.owner_id WHERE m.id = :i AND m.owner_id = :o "
                "AND m.session_id = :s AND m.role = 'assistant' AND m.response_type = 'clarify'"
            ),
            {"i": message_id, "o": owner_id, "s": session_id},
        )
    ).first()
    options = list(row[1] or []) if row is not None else []
    if row is None or not 0 <= option < len(options):
        return None
    return str(row[0]), uuid.UUID(options[option]["document_id"])


async def _staleness(
    db: AsyncSession, owner_id: uuid.UUID, msgs: list[Any]
) -> dict[uuid.UUID, dict[str, Any]]:
    """`{stale, since, newer_version_no}` for the marked messages: the newest current version number among the
    documents whose earlier version the run used (null when none is current any more)."""
    marked = {m["id"]: m for m in msgs if m["stale_since"] is not None}
    if not marked:
        return {}
    rows = (
        await db.execute(
            text(
                "SELECT m.id, max(cur.version_no) FROM chat_messages m "
                "JOIN run_versions rv ON rv.run_id = m.run_id "
                "JOIN document_versions v ON v.id = rv.version_id AND v.owner_id = :o "
                "JOIN documents d ON d.id = v.document_id AND d.owner_id = :o AND d.deleted_at IS NULL "
                "JOIN document_versions cur ON cur.id = d.current_version_id AND cur.owner_id = :o "
                "AND cur.version_no > v.version_no "
                "WHERE m.id = ANY(:ids) AND m.owner_id = :o GROUP BY m.id"
            ),
            {"o": owner_id, "ids": list(marked)},
        )
    ).all()
    newer: dict[uuid.UUID, int | None] = {r[0]: r[1] for r in rows}
    return {
        i: {"stale": True, "since": m["stale_since"].isoformat(), "newer_version_no": newer.get(i)}
        for i, m in marked.items()
    }


async def original_question(
    db: AsyncSession, owner_id: uuid.UUID, session_id: uuid.UUID, message_id: uuid.UUID
) -> str | None:
    """The question of the turn a message (user or assistant) belongs to, owner- and session-scoped; None when
    the message is not found (another owner's message is indistinguishable from a missing one)."""
    row = (
        await db.execute(
            text(
                "SELECT r.original_query FROM chat_messages m JOIN rag_runs r "
                "ON r.id = m.run_id AND r.owner_id = m.owner_id "
                "WHERE m.id = :i AND m.owner_id = :o AND m.session_id = :s"
            ),
            {"i": message_id, "o": owner_id, "s": session_id},
        )
    ).first()
    return None if row is None else str(row[0])


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
                    "s.verification, s.verification_reason, "
                    "e.chunk_id, e.sentence_start, e.sentence_end, e.cited_text, "
                    "c.page_start, c.page_end, c.section_path, c.version_id, v.version_no, "
                    "d.id AS document_id, d.title, (d.id IS NULL OR d.deleted_at IS NOT NULL) AS deleted "
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
            seg = {
                "ordinal": r["ordinal"],
                "text": r["text"],
                "support": r["support"],
                "verification": r["verification"],  # null: no check ran; see support_check.py
                "verification_reason": r["verification_reason"],
                "evidence": [],
            }
            by_segment[r["segment_id"]] = seg
            out.setdefault(r["message_id"], []).append(seg)
        if r["chunk_id"] is not None:
            gone = bool(r["deleted"])  # soft-deleted or purged: keep the segment text, redact the quote
            seg["evidence"].append(
                {
                    "chunk_id": str(r["chunk_id"]),
                    "document_id": str(r["document_id"]) if r["document_id"] else None,
                    "document_title": None if gone else r["title"],
                    "version_id": str(r["version_id"]) if r["version_id"] else None,
                    "version_no": r["version_no"],
                    "pages": [r["page_start"], r["page_end"]]
                    if r["page_start"]
                    else None,  # 0: flow document
                    "section_path": list(r["section_path"]) if r["section_path"] is not None else None,
                    "sentence_start": r["sentence_start"],
                    "sentence_end": r["sentence_end"],
                    "cited_text": None if gone else r["cited_text"],
                    "deleted": gone,
                }
            )
    return out


async def run_of_message(
    db: AsyncSession, owner_id: uuid.UUID, session_id: uuid.UUID, message_id: uuid.UUID
) -> uuid.UUID | None:
    """The run of the turn a message (user or assistant) belongs to, owner- and session-scoped."""
    row = (
        await db.execute(
            text("SELECT run_id FROM chat_messages WHERE id = :i AND owner_id = :o AND session_id = :s"),
            {"i": message_id, "o": owner_id, "s": session_id},
        )
    ).first()
    return None if row is None or row[0] is None else uuid.UUID(str(row[0]))
