"""Read side of the brief (design §12): the `GET /v1/documents/{id}/brief` body and the list snippet.

The brief of a document is the brief of its current version (else its newest). Every query is scoped by
`owner_id`; a missing, foreign or soft-deleted document is `None` (the route answers 404). A brief that has
not been produced yet is a normal 200 with `status: pending`.
"""

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.insights.brief import (
    EMPTY_PARAMS_HASH,
    KIND,
    PROMPT_VERSION,
    Brief,
    StoredEvidence,
    StoredFact,
    StoredTone,
)

SNIPPET_CHARS = 200


def summary_snippet(summary: str | None, limit: int = SNIPPET_CHARS) -> str | None:
    """The first `limit` characters of a summary (at most `limit` in total, ellipsis included)."""
    if not summary:
        return None
    s = " ".join(summary.split())
    if len(s) <= limit:
        return s
    cut = s[: limit - 1]
    cut = cut.rsplit(" ", 1)[0] if " " in cut else cut
    return cut.rstrip(" ,;:.") + "…"


def _evidence(e: StoredEvidence) -> dict[str, Any]:
    return {
        "chunk_id": str(e.chunk_id),
        "version_no": e.version_no,
        "pages": e.pages,
        "section_path": e.section_path,
        "sentence_start": e.sentence_start,
        "sentence_end": e.sentence_end,
        "cited_text": e.cited_text,
    }


def _tone(t: StoredTone | None, include_unverified: bool) -> dict[str, Any] | None:
    if t is None or (t.status != "verified" and not include_unverified):
        return None
    return {
        "label": t.label,
        "note": t.note,
        "status": t.status,
        "evidence": [_evidence(e) for e in t.evidence],
    }


def _fact(f: StoredFact) -> dict[str, Any]:
    return {
        "field": f.field,
        "value": f.value,
        "status": f.status,
        "evidence": [_evidence(e) for e in f.evidence],
    }


def empty_brief(status: str, version_id: uuid.UUID | None) -> dict[str, Any]:
    return {
        "status": status,
        "version_id": str(version_id) if version_id else None,
        "document_type": None,
        "category": None,
        "tags": [],
        "summary": None,
        "suggested_questions": [],
        "facts": [],
        "tone": None,
        "model": None,
        "created_at": None,
    }


def brief_body(
    content: dict[str, Any],
    version_id: uuid.UUID,
    model: str | None,
    created_at: Any,
    *,
    include_unverified: bool,
) -> dict[str, Any]:
    brief = Brief.model_validate(content)
    facts = [f for f in brief.facts if include_unverified or f.status == "verified"]
    return {
        "status": "partial" if brief.partial else "ready",
        "version_id": str(version_id),
        "document_type": brief.document_type,
        "category": brief.category,
        "tags": brief.tags,
        "summary": brief.summary,
        "suggested_questions": brief.suggested_questions,
        "facts": [_fact(f) for f in facts],
        "tone": _tone(brief.tone, include_unverified),
        "model": model,
        "created_at": created_at.isoformat() if created_at else None,
    }


async def load_brief(
    db: AsyncSession, owner_id: uuid.UUID, document_id: uuid.UUID, *, include_unverified: bool = True
) -> dict[str, Any] | None:
    doc = (
        await db.execute(
            text(
                "SELECT d.current_version_id, (SELECT v.id FROM document_versions v "
                "WHERE v.document_id = d.id AND v.owner_id = d.owner_id "
                "ORDER BY v.version_no DESC LIMIT 1) AS newest_version_id "
                "FROM documents d WHERE d.id = :d AND d.owner_id = :o AND d.deleted_at IS NULL"
            ),
            {"d": document_id, "o": owner_id},
        )
    ).first()
    if doc is None:
        return None
    version_id: uuid.UUID | None = doc.current_version_id or doc.newest_version_id
    if version_id is None:
        return empty_brief("pending", None)
    row = (
        await db.execute(
            text(
                "SELECT status, content, model, created_at FROM document_insights "
                "WHERE owner_id = :o AND version_id = :v AND kind = :k AND params_hash = :h "
                "AND prompt_version = :pv"
            ),
            {"o": owner_id, "v": version_id, "k": KIND, "h": EMPTY_PARAMS_HASH, "pv": PROMPT_VERSION},
        )
    ).first()
    if row is None or row.status not in ("done", "failed"):
        return empty_brief("pending", version_id)
    if row.status == "failed" or not row.content:
        return empty_brief("failed", version_id)
    return brief_body(
        row.content, version_id, row.model, row.created_at, include_unverified=include_unverified
    )
