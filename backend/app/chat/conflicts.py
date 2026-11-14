"""Conflicting sources: the model flags disagreeing evidence, code validates it.

The answer schema carries `conflicts: [{topic, handles_a[], handles_b[]}]`. Handles resolve through the same
manifest as citations (`cited_text` is copied from storage, never taken from the model). A conflict survives
only when both sides keep at least one valid handle and the two sides together touch at least two distinct
chunks: a "disagreement" inside one passage is not a conflict between sources. What survives is stored in
`rag_runs.conflicts` as chunk references plus the copied sentence text, and read back with the document
titles, owner-scoped; a side whose document was deleted is redacted and a conflict left without both sides
is not shown.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.citations import EvidenceReference, resolve_segment
from app.chat.evidence import EvidenceSet

CONFLICT_DETECTED = "CONFLICT_DETECTED"
MAX_CONFLICTS = 3
MAX_TOPIC_CHARS = 120


@dataclass(frozen=True)
class ResolvedConflict:
    topic: str
    side_a: tuple[EvidenceReference, ...]
    side_b: tuple[EvidenceReference, ...]


def resolve_conflicts(raw: Sequence[Any], evidence: EvidenceSet) -> tuple[ResolvedConflict, ...]:
    """Keep the conflicts whose two sides both resolve and together cite >= 2 distinct chunks."""
    kept: list[ResolvedConflict] = []
    for item in raw:
        topic = " ".join(str(item.topic).split())[:MAX_TOPIC_CHARS]
        a = resolve_segment(0, {"text": topic, "evidence": list(item.handles_a)}, evidence).references
        b = resolve_segment(0, {"text": topic, "evidence": list(item.handles_b)}, evidence).references
        chunks = {r.chunk_id for r in (*a, *b)}
        if topic and a and b and len(chunks) >= 2:
            kept.append(ResolvedConflict(topic, a, b))
    return tuple(kept[:MAX_CONFLICTS])


def conflicts_record(conflicts: Sequence[ResolvedConflict]) -> dict[str, Any] | None:
    """The `rag_runs.conflicts` value (None when the turn had none)."""

    def refs(side: Sequence[EvidenceReference]) -> list[dict[str, Any]]:
        return [
            {
                "chunk_id": str(r.chunk_id),
                "sentence_start": r.sentence_start,
                "sentence_end": r.sentence_end,
                "cited_text": r.cited_text,
            }
            for r in side
        ]

    if not conflicts:
        return None
    return {"items": [{"topic": c.topic, "a": refs(c.side_a), "b": refs(c.side_b)} for c in conflicts]}


class EvidenceView(BaseModel):
    """A cited sentence of a conflict side, in the shape of a segment's evidence."""

    chunk_id: str
    document_id: str | None
    document_title: str | None
    version_no: int | None
    pages: list[int] | None
    sentence_start: int
    sentence_end: int
    cited_text: str


class ConflictView(BaseModel):
    """What history and the stream's `done` event expose: a topic and the evidence of both sides."""

    topic: str
    side_a: list[EvidenceView]
    side_b: list[EvidenceView]


async def load_conflicts(
    db: AsyncSession, owner_id: uuid.UUID, run_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, list[ConflictView]]:
    """Stored conflicts of the given runs with live document titles; owner-scoped (another owner's run reads
    as having none). Runs without conflicts are absent from the result."""
    if not run_ids:
        return {}
    rows = (
        await db.execute(
            text(
                "SELECT id, conflicts FROM rag_runs "
                "WHERE owner_id = :o AND id = ANY(:r) AND conflicts IS NOT NULL"
            ),
            {"o": owner_id, "r": list(run_ids)},
        )
    ).all()
    raw = {r[0]: (r[1] or {}).get("items", []) for r in rows}
    chunk_ids = {
        uuid.UUID(ref["chunk_id"]) for items in raw.values() for it in items for ref in (*it["a"], *it["b"])
    }
    info: dict[uuid.UUID, Any] = {}
    if chunk_ids:
        got = (
            await db.execute(
                text(
                    "SELECT c.id, d.id, d.title, v.version_no, c.page_start, c.page_end "
                    "FROM chunks c JOIN document_versions v ON v.id = c.version_id AND v.owner_id = :o "
                    "JOIN documents d ON d.id = v.document_id AND d.owner_id = :o AND d.deleted_at IS NULL "
                    "WHERE c.owner_id = :o AND c.id = ANY(:c)"
                ),
                {"o": owner_id, "c": list(chunk_ids)},
            )
        ).all()
        info = {r[0]: r for r in got}

    def view(ref: dict[str, Any]) -> EvidenceView | None:
        r = info.get(uuid.UUID(ref["chunk_id"]))
        if r is None:  # the document is deleted or purged: the quote is redacted
            return None
        return EvidenceView(
            chunk_id=ref["chunk_id"],
            document_id=str(r[1]),
            document_title=r[2],
            version_no=r[3],
            pages=[r[4], r[5]] if r[4] else None,
            sentence_start=ref["sentence_start"],
            sentence_end=ref["sentence_end"],
            cited_text=ref["cited_text"],
        )

    out: dict[uuid.UUID, list[ConflictView]] = {}
    for run_id, items in raw.items():
        for it in items:
            a = [v for v in map(view, it["a"]) if v is not None]
            b = [v for v in map(view, it["b"]) if v is not None]
            if a and b:
                out.setdefault(run_id, []).append(ConflictView(topic=it["topic"], side_a=a, side_b=b))
    return out
