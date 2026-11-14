"""Meta route (design 8.3, S18.05): questions about the vault, answered by allowlisted SQL.

`analyze_query` returns a loosely typed `meta_op` (`MetaOpOut`); `validate_op` turns it into a `MetaOp` or
rejects it (`META_OP_REJECTED`, the turn then routes as qa). `run_op` runs one parameterized, owner-scoped
query per op and composes the answer in code from the rows. The model never writes SQL and never sees rows.
Key dates cite the verified brief facts' stored evidence (`cited_text` comes from stored sentences).
"""

import datetime as dt
import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.citations import EvidenceReference, ResolvedSegment
from app.insights.brief import EMPTY_PARAMS_HASH, KIND, PROMPT_VERSION

META_OP_REJECTED = "META_OP_REJECTED"
META_COUNT_DOCUMENTS = "META_COUNT_DOCUMENTS"
META_LIST_DOCUMENTS = "META_LIST_DOCUMENTS"
META_DOCUMENTS_MENTIONING = "META_DOCUMENTS_MENTIONING"
META_LIST_KEY_DATES = "META_LIST_KEY_DATES"
META_DOCUMENT_INFO = "META_DOCUMENT_INFO"

OP_CODES: dict[str, str] = {
    "COUNT_DOCUMENTS": META_COUNT_DOCUMENTS,
    "LIST_DOCUMENTS": META_LIST_DOCUMENTS,
    "DOCUMENTS_MENTIONING": META_DOCUMENTS_MENTIONING,
    "LIST_KEY_DATES": META_LIST_KEY_DATES,
    "DOCUMENT_INFO": META_DOCUMENT_INFO,
}
STATUSES = ("uploaded", "processing", "ready", "failed", "needs_ocr", "unsupported")
DATE_FIELDS = ("effective_date", "expiry_date", "deadline")
MAX_LIST = 20
MAX_TERM_CHARS = 80
MAX_FILTER_CHARS = 60
MAX_DATE_ROWS = 20


class MetaOpOut(BaseModel):
    """Strict-schema shape of the model choice: every field required, `T | None` only. `op` is a plain string
    so an unknown op reaches `validate_op` (and `META_OP_REJECTED`) instead of failing the schema."""

    model_config = ConfigDict(extra="forbid")
    op: str
    status: str | None
    category: str | None
    tag: str | None
    term: str | None
    field: str | None
    before: str | None
    after: str | None
    document_ref: str | None
    limit: int | None


@dataclass(frozen=True)
class MetaOp:
    op: str
    status: str | None = None
    category: str | None = None
    tag: str | None = None
    term: str | None = None
    field: str | None = None
    before: dt.date | None = None
    after: dt.date | None = None
    document_id: uuid.UUID | None = None
    limit: int = MAX_LIST


@dataclass(frozen=True)
class MetaAnswer:
    segments: tuple[ResolvedSegment, ...]
    reason: str


def _s(v: str | None, n: int) -> str | None:
    v = " ".join((v or "").split())[:n]
    return v or None


def _iso(v: str | None) -> dt.date | None:
    try:
        return dt.date.fromisoformat(v.strip()) if v and v.strip() else None
    except ValueError:
        return None


def validate_op(out: MetaOpOut | None, doc_keys: Mapping[str, uuid.UUID]) -> MetaOp | None:
    """Allowlist check. None means rejected: unknown op, missing or unusable required parameter, unparseable
    date, unknown status/field, or a document ref that is not one of `doc_keys` (the ids stay in code)."""
    if out is None or out.op not in OP_CODES:
        return None
    op = out.op
    status = (_s(out.status, 20) or "").lower() or None
    field = (_s(out.field, 20) or "").lower() or None
    before, after = _iso(out.before), _iso(out.after)
    if (out.before and before is None) or (out.after and after is None):
        return None
    if (status is not None and status not in STATUSES) or (field is not None and field not in DATE_FIELDS):
        return None
    term = _s(out.term, MAX_TERM_CHARS)
    doc_id = doc_keys.get((out.document_ref or "").strip())
    if (op == "DOCUMENTS_MENTIONING" and term is None) or (op == "DOCUMENT_INFO" and doc_id is None):
        return None
    return MetaOp(
        op,
        status=status,
        category=(_s(out.category, MAX_FILTER_CHARS) or "").lower() or None,
        tag=(_s(out.tag, MAX_FILTER_CHARS) or "").lower() or None,
        term=term,
        field=field,
        before=before,
        after=after,
        document_id=doc_id,
        limit=min(max(out.limit or MAX_LIST, 1), MAX_LIST),
    )


def _plain(items: Sequence[str]) -> tuple[ResolvedSegment, ...]:
    return tuple(ResolvedSegment(i, t, "none", ()) for i, t in enumerate(items))


def _noun(n: int) -> str:
    return f"{n} document" + ("" if n == 1 else "s")


_BASE = (
    "FROM documents d JOIN document_versions v ON v.id = d.current_version_id AND v.owner_id = d.owner_id "
    "WHERE d.owner_id = :o AND d.deleted_at IS NULL "
    "AND (CAST(:status AS text) IS NULL OR v.status = :status "
    "OR (:status = 'ready' AND v.status = 'enriched')) "
    "AND (CAST(:category AS text) IS NULL OR lower(d.category) = :category) "
    "AND (CAST(:tag AS text) IS NULL OR :tag = ANY(d.tags))"
)


def _params(owner_id: uuid.UUID, op: MetaOp) -> dict[str, Any]:
    return {"o": owner_id, "status": op.status, "category": op.category, "tag": op.tag}


async def _count(db: AsyncSession, owner_id: uuid.UUID, op: MetaOp) -> MetaAnswer:
    n = (await db.execute(text(f"SELECT count(*) {_BASE}"), _params(owner_id, op))).scalar_one()
    parts = [
        p for p in (op.status, op.category and f"category '{op.category}'", op.tag and f"tag '{op.tag}'") if p
    ]
    qual = f" ({', '.join(parts)})" if parts else ""
    return MetaAnswer(_plain([f"You have {_noun(n)}{qual}."]), META_COUNT_DOCUMENTS)


async def _list(db: AsyncSession, owner_id: uuid.UUID, op: MetaOp) -> MetaAnswer:
    rows = (
        await db.execute(
            text(f"SELECT d.title, d.category {_BASE} ORDER BY d.created_at DESC, d.id LIMIT :n"),
            {**_params(owner_id, op), "n": op.limit},
        )
    ).all()
    if not rows:
        return MetaAnswer(_plain(["No documents match."]), META_LIST_DOCUMENTS)
    items = [t + (f" ({c})" if c else "") for t, c in rows]
    return MetaAnswer(_plain([f"{_noun(len(rows))}: " + "; ".join(items) + "."]), META_LIST_DOCUMENTS)


async def _mentioning(
    db: AsyncSession, owner_id: uuid.UUID, op: MetaOp, version_ids: Sequence[uuid.UUID]
) -> MetaAnswer:
    rows = (
        await db.execute(
            text(
                "SELECT d.title FROM documents d WHERE d.owner_id = :o AND d.deleted_at IS NULL "
                "AND d.current_version_id = ANY(:v) AND EXISTS (SELECT 1 FROM chunks c "
                "WHERE c.owner_id = :o AND c.version_id = d.current_version_id "
                "AND c.tsv_en @@ plainto_tsquery('english', :t)) ORDER BY d.title, d.id LIMIT :n"
            ),
            {"o": owner_id, "v": list(version_ids), "t": op.term, "n": op.limit},
        )
    ).all()
    if not rows:
        return MetaAnswer(_plain([f"No documents mention '{op.term}'."]), META_DOCUMENTS_MENTIONING)
    verb = "mentions" if len(rows) == 1 else "mention"
    body = f"{_noun(len(rows))} {verb} '{op.term}': " + "; ".join(r[0] for r in rows)
    return MetaAnswer(_plain([body + "."]), META_DOCUMENTS_MENTIONING)


_MONTHS = [
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
]
_TEXT_DATE = re.compile(r"\b([A-Za-z]{3,9})\.? (\d{1,2}),? (\d{4})\b")
_DMY_DATE = re.compile(r"\b(\d{1,2}) ([A-Za-z]{3,9})\.?,? (\d{4})\b")
_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_US_DATE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")


def _month(name: str) -> int | None:
    n = name.lower()
    for i, m in enumerate(_MONTHS, 1):
        if m == n or (len(n) == 3 and m.startswith(n)):
            return i
    return None


def parse_date(value: str) -> dt.date | None:
    """First calendar date in a fact value (June 1, 2020 / 1 June 2020 / 2020-06-01 / 6/1/2020), else None."""
    try:
        if m := _ISO_DATE.search(value):
            return dt.date(int(m[1]), int(m[2]), int(m[3]))
        if m := _TEXT_DATE.search(value):
            mo = _month(m[1])
            return dt.date(int(m[3]), mo, int(m[2])) if mo else None
        if m := _DMY_DATE.search(value):
            mo = _month(m[2])
            return dt.date(int(m[3]), mo, int(m[1])) if mo else None
        if m := _US_DATE.search(value):
            return dt.date(int(m[3]), int(m[1]), int(m[2]))
    except ValueError:
        return None
    return None


def _in_window(d: dt.date | None, op: MetaOp) -> bool:
    if op.before is None and op.after is None:
        return True
    if d is None:
        return False
    return (op.before is None or d < op.before) and (op.after is None or d > op.after)


async def _key_dates(
    db: AsyncSession, owner_id: uuid.UUID, op: MetaOp, version_ids: Sequence[uuid.UUID]
) -> MetaAnswer:
    rows = (
        await db.execute(
            text(
                "SELECT d.title, i.content -> 'facts' FROM document_insights i "
                "JOIN document_versions v ON v.id = i.version_id AND v.owner_id = i.owner_id "
                "JOIN documents d ON d.id = v.document_id AND d.owner_id = i.owner_id "
                "AND d.current_version_id = v.id AND d.deleted_at IS NULL "
                "WHERE i.owner_id = :o AND i.version_id = ANY(:v) AND i.kind = :k AND i.params_hash = :h "
                "AND i.prompt_version = :pv AND i.status = 'done' ORDER BY d.title, d.id"
            ),
            {"o": owner_id, "v": list(version_ids), "k": KIND, "h": EMPTY_PARAMS_HASH, "pv": PROMPT_VERSION},
        )
    ).all()
    wanted = (op.field,) if op.field else DATE_FIELDS
    found: list[tuple[dt.date | None, str, Any]] = []
    for title, facts in rows:
        for f in facts or []:
            if f.get("field") in wanted and f.get("status") == "verified":
                d = parse_date(f["value"])
                if _in_window(d, op):
                    found.append((d, title, f))
    found.sort(key=lambda x: (x[0] is None, x[0] or dt.date.min, x[1]))
    if not found:
        return MetaAnswer(_plain(["No matching dates were found in your documents."]), META_LIST_KEY_DATES)
    segs: list[ResolvedSegment] = []
    for i, (_, title, f) in enumerate(found[:MAX_DATE_ROWS]):
        refs = tuple(
            EvidenceReference(
                uuid.UUID(str(e["chunk_id"])), uuid.UUID(str(e["version_id"])), e["sentence_start"],
                e["sentence_end"], e["char_start"], e["char_end"], e["cited_text"],
            )
            for e in f.get("evidence", [])
        )  # fmt: skip
        label = str(f["field"]).replace("_", " ")
        segs.append(ResolvedSegment(i, f"{title}: {label} {f['value']}.", "cited" if refs else "none", refs))
    return MetaAnswer(tuple(segs), META_LIST_KEY_DATES)


async def _info(db: AsyncSession, owner_id: uuid.UUID, op: MetaOp) -> MetaAnswer:
    row = (
        await db.execute(
            text(
                "SELECT d.title, d.category, d.tags, v.status, v.version_no, v.page_count, "
                "(SELECT count(*) FROM document_versions x WHERE x.document_id = d.id AND x.owner_id = :o) "
                "FROM documents d JOIN document_versions v ON v.id = d.current_version_id "
                "AND v.owner_id = d.owner_id WHERE d.owner_id = :o AND d.id = :d AND d.deleted_at IS NULL"
            ),
            {"o": owner_id, "d": op.document_id},
        )
    ).first()
    if row is None:
        return MetaAnswer(_plain(["That document was not found."]), META_DOCUMENT_INFO)
    title, cat, tags, status, vno, pages, nver = row
    bits = [f"status {status}", f"{nver} version{'s' if nver != 1 else ''} (current v{vno})"]
    if pages:
        bits.append(f"{pages} page{'s' if pages != 1 else ''}")
    if cat:
        bits.append(f"category {cat}")
    if tags:
        bits.append("tags " + ", ".join(tags))
    return MetaAnswer(_plain([f"'{title}': " + "; ".join(bits) + "."]), META_DOCUMENT_INFO)


async def run_op(
    db: AsyncSession, owner_id: uuid.UUID, op: MetaOp, scope_version_ids: Sequence[uuid.UUID]
) -> MetaAnswer:
    """Run one allowlisted op. Counts, lists and info cover the owner's live documents; term and date ops read
    the ready content in `scope_version_ids`. Every statement filters on `owner_id`."""
    if op.op == "COUNT_DOCUMENTS":
        return await _count(db, owner_id, op)
    if op.op == "LIST_DOCUMENTS":
        return await _list(db, owner_id, op)
    if op.op == "DOCUMENTS_MENTIONING":
        return await _mentioning(db, owner_id, op, scope_version_ids)
    if op.op == "LIST_KEY_DATES":
        return await _key_dates(db, owner_id, op, scope_version_ids)
    return await _info(db, owner_id, op)
