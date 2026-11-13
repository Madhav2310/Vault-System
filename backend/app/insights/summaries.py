"""Custom summaries (design §9.2, plan S12.06): `POST /v1/documents/{id}/summaries`, run on the `ai` queue.

- Parameters `{length, focus, tone}` are canonicalized (focus: whitespace collapsed, empty -> none), hashed;
  a summary is cached by `(version, kind='summary', params_hash, prompt_version)` in `document_insights`, so
  the same request again is a cache hit and makes no provider call.
- Row states: `pending` (queued) -> `running` (claimed by a worker; the claim carries a lease) -> `done` |
  `failed`. A claim is a compare-and-set, so a redelivered task never produces a second model call while the
  first still holds its lease; `created_at` is bumped at every transition and doubles as the lease clock (the
  table has no `updated_at`). A `done` row is never overwritten. A `failed` row is retried by the next POST.
- Evidence: the whole current version when it fits `SUMMARY_MAX_EVIDENCE_TOKENS`; otherwise the chunks the
  retriever ranks for the focus (or "overview"), in rank order up to the budget and topped up with chunks
  spread evenly over the document, presented in document order. A retrieval failure degrades to the spread
  alone, so a large document never fails.
- Output is the chat answer-segment schema: the model returns handles, `citations.resolve` turns them into
  stored sentences (`cited_text` is never model-written), a segment without a valid handle is kept and marked
  `support: none`. Length is a code decision as well: trailing segments past the word limit are dropped.
- `focus` is untrusted: it goes into its own delimited block with delimiter-like tags defused.
- No DB connection is held across the model call (each step opens a short session); document text is never
  logged.
"""

import asyncio
import json
import re
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.errors import DomainError
from app.chat.citations import EvidenceReference, resolve
from app.chat.evidence import EvidenceChunk, EvidenceSet, answer_schema, build_evidence, cache_mode_for
from app.chat.nodes_common import defuse
from app.core.error_codes import ErrorCode
from app.core.ids import uuid7
from app.core.logging import get_logger
from app.insights.brief import StoredEvidence, params_hash
from app.llm.errors import LlmError
from app.llm.gateway import LlmGateway
from app.retrieval.load import load_evidence_chunks
from app.retrieval.scope import READY_STATUSES, Scope

KIND = "summary"  # document_insights.kind
PROMPT: tuple[str, int] = ("summary_custom", 1)
PURPOSE = "summary"  # gateway node: routed to role `balanced` (routing.DEFAULT_ROUTES)
SUMMARY_TASK = "vault.ai.summary"

Length = Literal["short", "medium", "long"]
Tone = Literal["neutral", "executive", "plain"]
MAX_FOCUS_CHARS = 200
WORD_LIMIT: dict[str, int] = {"short": 80, "medium": 200, "long": 450}
WORD_TARGET: dict[str, tuple[int, int]] = {"short": (40, 80), "medium": (120, 200), "long": (250, 450)}
SUMMARY_MAX_EVIDENCE_TOKENS = 12_000
OVERVIEW_QUERY = "overview"
LEASE_S = 900  # a `running` or `pending` row older than this is presumed abandoned and may be claimed again

# reason codes stored with the summary
SUMMARY_EVIDENCE_SUBSET = (
    "SUMMARY_EVIDENCE_SUBSET"  # a large document: only part of it was shown to the model
)
SUMMARY_RETRIEVAL_DEGRADED = (
    "SUMMARY_RETRIEVAL_DEGRADED"  # retrieval failed; evidence is spread over the document
)
SUMMARY_TRIMMED = "SUMMARY_TRIMMED"  # trailing segments dropped to meet the word limit
SUMMARY_OVER_LENGTH = "SUMMARY_OVER_LENGTH"  # the first segment alone exceeds the word limit
SUMMARY_NO_EVIDENCE = "SUMMARY_NO_EVIDENCE"  # the model abstained: nothing relevant in the evidence
SUMMARY_GENERATION_INVALID = "SUMMARY_GENERATION_INVALID"
CITATION_UNRESOLVED = "CITATION_UNRESOLVED"

_log = get_logger("vault.insights.summary")
_TAGS = re.compile(r"</?\s*(focus|request|evidence)\s*>", re.IGNORECASE)


# --- parameters ---------------------------------------------------------------------------------------------


class SummaryRequest(BaseModel):
    """The POST body. `length` is required; an unknown field or value is a 422."""

    model_config = ConfigDict(extra="forbid")
    length: Length
    focus: str | None = Field(default=None, max_length=MAX_FOCUS_CHARS)
    tone: Tone = "neutral"

    @field_validator("focus")
    @classmethod
    def _focus(cls, v: str | None) -> str | None:
        return " ".join(v.split()) or None if v is not None else None

    def params(self) -> dict[str, Any]:
        return {"length": self.length, "focus": self.focus, "tone": self.tone}


def summary_params_hash(params: Mapping[str, Any]) -> str:
    """sha256 of the canonical JSON of `{length, focus, tone}`."""
    return params_hash({"length": params["length"], "focus": params["focus"], "tone": params["tone"]})


def word_count(s: str) -> int:
    return len(s.split())


# --- evidence selection -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ChunkRef:
    id: uuid.UUID
    ordinal: int
    tokens: int


def spread(refs: Sequence[ChunkRef], budget: int) -> list[ChunkRef]:
    """Chunks spread evenly over `refs` (document order), at most `budget` tokens in total. Everything when it
    fits. Deterministic."""
    total = sum(r.tokens for r in refs)
    if total <= budget:
        return list(refs)
    if budget <= 0:
        return []
    frac = budget / total
    acc = 1.0  # the first chunk is always a candidate: it carries the title, parties and date
    used = 0
    out: list[ChunkRef] = []
    for r in refs:
        acc += frac
        if acc >= 1.0:
            acc -= 1.0
            if used + r.tokens <= budget:
                out.append(r)
                used += r.tokens
    return out


@dataclass(frozen=True)
class EvidencePlan:
    ids: tuple[uuid.UUID, ...]  # document order
    complete: bool  # the whole version is shown
    degraded: bool  # retrieval was needed and failed or returned nothing usable


def plan_evidence(refs: Sequence[ChunkRef], ranked: Sequence[uuid.UUID] | None, budget: int) -> EvidencePlan:
    """Which chunks to show. `ranked` is the retriever's order (best first), or None when retrieval was not
    run (the document fits) or failed. Ranked chunks fill the budget first, then spread chunks fill what is
    left; the result is in document order."""
    ordered = sorted(refs, key=lambda r: r.ordinal)
    if sum(r.tokens for r in ordered) <= budget:
        return EvidencePlan(tuple(r.id for r in ordered), True, False)
    by_id = {r.id: r for r in ordered}
    chosen: dict[uuid.UUID, ChunkRef] = {}
    used = 0
    for cid in ranked or ():
        r = by_id.get(cid)
        if r is None or cid in chosen:
            continue
        if used + r.tokens > budget and chosen:
            continue
        chosen[cid] = r
        used += r.tokens
    rest = [r for r in ordered if r.id not in chosen]
    for r in spread(rest, budget - used):
        chosen[r.id] = r
    picked = sorted(chosen.values(), key=lambda r: r.ordinal)
    return EvidencePlan(tuple(r.id for r in picked), False, not ranked)


# --- generation ---------------------------------------------------------------------------------------------


class NoChunksError(Exception):
    pass


LoadChunks = Callable[[Sequence[uuid.UUID]], Awaitable[list[EvidenceChunk]]]
Select = Callable[[uuid.UUID, uuid.UUID, uuid.UUID, str], Awaitable[Sequence[uuid.UUID]]]
"""(owner, document, version, query) -> ranked chunk ids, best first."""


def compose_message(evidence: EvidenceSet, params: Mapping[str, Any]) -> str:
    """Evidence first, then the request block (code-chosen enums), then the user's focus in its own block."""
    length = params["length"]
    lo, hi = WORD_TARGET[length]
    parts = [
        evidence.text,
        "<request>\n"
        f"length: {length}\nword_limit: {WORD_LIMIT[length]}\nword_target: {lo}-{hi}\n"
        f"tone: {params['tone']}\n"
        "</request>",
    ]
    if params["focus"]:
        focus = _TAGS.sub("[tag removed]", defuse(" ".join(str(params["focus"]).split())))
        parts.append(f"<focus>\n{focus}\n</focus>")
    return "\n\n".join(parts)


@dataclass(frozen=True)
class StoredSegment:
    ordinal: int
    text: str
    support: str  # cited | none
    citations: tuple[StoredEvidence, ...]


def trim_to_limit(
    segments: Sequence[StoredSegment], limit: int
) -> tuple[list[StoredSegment], tuple[str, ...]]:
    """Whole trailing segments are dropped until the total is within `limit` words; the first segment always
    stays (`SUMMARY_OVER_LENGTH` when it alone is over)."""
    kept: list[StoredSegment] = []
    total = 0
    for seg in segments:
        n = word_count(seg.text)
        if kept and total + n > limit:
            break
        kept.append(seg)
        total += n
    reasons: list[str] = []
    if len(kept) < len(segments):
        reasons.append(SUMMARY_TRIMMED)
    if kept and total > limit:
        reasons.append(SUMMARY_OVER_LENGTH)
    return [StoredSegment(i, s.text, s.support, s.citations) for i, s in enumerate(kept)], tuple(reasons)


def _stored(ref: EvidenceReference, chunk: EvidenceChunk) -> StoredEvidence:
    pages = list(range(chunk.page_start, chunk.page_end + 1)) if chunk.page_start else []
    return StoredEvidence(
        chunk_id=ref.chunk_id,
        version_id=ref.version_id,
        version_no=chunk.version_no,
        pages=pages,
        section_path=list(chunk.section_path),
        sentence_start=ref.sentence_start,
        sentence_end=ref.sentence_end,
        char_start=ref.char_start,
        char_end=ref.char_end,
        cited_text=ref.cited_text,
    )


@dataclass(frozen=True)
class Generated:
    segments: tuple[StoredSegment, ...]
    reasons: tuple[str, ...]
    model: str
    cost_usd: Decimal
    evidence_chunks: int


async def generate_summary(
    gateway: LlmGateway,
    load: LoadChunks,
    *,
    owner_id: uuid.UUID,
    params: Mapping[str, Any],
    plan: EvidencePlan,
) -> Generated:
    """One `summary` call over the planned chunks. Raises `LlmError` (the caller records a failed row) or
    `NoChunksError`."""
    chunks = await load(list(plan.ids))
    if not chunks:
        raise NoChunksError("the version has no chunks to summarize")
    evidence = build_evidence(chunks)
    schema = answer_schema(evidence, "rag")
    res = await gateway.call(
        purpose=PURPOSE,
        lane="background",
        prompt=PROMPT,
        owner_id=owner_id,
        schema=schema,
        messages=[{"role": "user", "content": compose_message(evidence, params)}],
        cache_mode=cache_mode_for("rag"),
    )
    assert res.output is not None
    reasons: list[str] = []
    if not plan.complete:
        reasons.append(SUMMARY_EVIDENCE_SUBSET)
    if plan.degraded:
        reasons.append(SUMMARY_RETRIEVAL_DEGRADED)
    out: Any = res.output
    if out.status == "abstain" or not out.segments:
        reasons.append(SUMMARY_NO_EVIDENCE)
        return Generated((), tuple(reasons), res.model, res.cost_usd, len(chunks))
    resolution = resolve(out.segments, evidence)
    reasons += list(resolution.reasons)
    segments = [
        StoredSegment(
            s.ordinal,
            s.text,
            s.support,
            tuple(_stored(r, evidence.chunks[r.chunk_id]) for r in s.references),
        )
        for s in resolution.segments
    ]
    segments, trim_reasons = trim_to_limit(segments, WORD_LIMIT[params["length"]])
    reasons += trim_reasons
    return Generated(tuple(segments), tuple(dict.fromkeys(reasons)), res.model, res.cost_usd, len(chunks))


def content_of(g: Generated) -> dict[str, Any]:
    return {
        "segments": [
            {
                "ordinal": s.ordinal,
                "text": s.text,
                "support": s.support,
                "citations": [c.model_dump(mode="json") for c in s.citations],
            }
            for s in g.segments
        ],
        "word_count": sum(word_count(s.text) for s in g.segments),
        "reasons": list(g.reasons),
        "evidence_chunks": g.evidence_chunks,
        "cost_usd": str(g.cost_usd),
    }


# --- read side ----------------------------------------------------------------------------------------------


def poll_path(document_id: uuid.UUID, summary_id: uuid.UUID) -> str:
    return f"/v1/documents/{document_id}/summaries/{summary_id}"


def _citation(c: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "chunk_id": c["chunk_id"],
        "version_no": c["version_no"],
        "pages": c["pages"],
        "section_path": c["section_path"],
        "sentence_start": c["sentence_start"],
        "sentence_end": c["sentence_end"],
        "cited_text": c["cited_text"],
    }


def summary_view(row: Mapping[str, Any], document_id: uuid.UUID) -> dict[str, Any]:
    """The API body of one `document_insights` summary row."""
    sid = row["id"]
    base: dict[str, Any] = {
        "id": str(sid),
        "document_id": str(document_id),
        "version_id": str(row["version_id"]),
        "params": row["params"],
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
    }
    status = row["status"]
    if status == "done":
        c = row["content"] or {}
        return {
            **base,
            "status": "ready",
            "segments": [
                {
                    "ordinal": s["ordinal"],
                    "text": s["text"],
                    "support": s["support"],
                    "citations": [_citation(x) for x in s["citations"]],
                }
                for s in c.get("segments", [])
            ],
            "word_count": c.get("word_count", 0),
            "reasons": c.get("reasons", []),
            "model": row["model"],
        }
    if status == "failed":
        return {
            **base,
            "status": "failed",
            "error_code": (row["content"] or {}).get("error_code", "internal"),
        }
    return {**base, "status": "pending", "poll": poll_path(document_id, sid)}


_ROW = (
    "SELECT i.id, i.version_id, i.params, i.content, i.status, i.model, i.created_at FROM document_insights i"
)


async def load_summary(
    db: AsyncSession, owner_id: uuid.UUID, document_id: uuid.UUID, summary_id: uuid.UUID
) -> dict[str, Any] | None:
    """The summary body, or None when the owner has no live document with this summary."""
    row = (
        (
            await db.execute(
                text(
                    _ROW + " JOIN document_versions v ON v.id = i.version_id AND v.owner_id = i.owner_id "
                    "JOIN documents d ON d.id = v.document_id AND d.owner_id = v.owner_id "
                    "WHERE i.id = :s AND i.owner_id = :o AND i.kind = :k AND d.id = :d "
                    "AND d.deleted_at IS NULL"
                ),
                {"s": summary_id, "o": owner_id, "k": KIND, "d": document_id},
            )
        )
        .mappings()
        .first()
    )
    return summary_view(dict(row), document_id) if row else None


# --- request side (POST) ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Requested:
    body: dict[str, Any]
    ready: bool  # cached: answer 200
    summary_id: uuid.UUID
    enqueue: bool  # the caller must enqueue the task after this call


async def request_summary(
    db: AsyncSession, owner_id: uuid.UUID, document_id: uuid.UUID, req: SummaryRequest
) -> Requested | None:
    """Look up or create the summary row for the document's current version. None: no such live document of
    this owner (404). A document with no ready current version raises a 409."""
    doc = (
        await db.execute(
            text(
                "SELECT d.current_version_id AS vid, v.status AS vstatus FROM documents d "
                "LEFT JOIN document_versions v ON v.id = d.current_version_id AND v.owner_id = d.owner_id "
                "WHERE d.id = :d AND d.owner_id = :o AND d.deleted_at IS NULL"
            ),
            {"d": document_id, "o": owner_id},
        )
    ).first()
    if doc is None:
        return None
    if doc.vid is None or doc.vstatus not in READY_STATUSES:
        raise DomainError(ErrorCode.CONFLICT, "the document is not ready yet")
    params = req.params()
    key = {
        "o": owner_id,
        "v": doc.vid,
        "k": KIND,
        "h": summary_params_hash(params),
        "pv": PROMPT[1],
    }
    sel = text(
        _ROW + " WHERE i.owner_id = :o AND i.version_id = :v AND i.kind = :k AND i.params_hash = :h "
        "AND i.prompt_version = :pv"
    )
    row = (await db.execute(sel, key)).mappings().first()
    enqueue = False
    if row is None:
        sid = uuid7()
        inserted = (
            await db.execute(
                text(
                    "INSERT INTO document_insights (id, owner_id, version_id, kind, params_hash, params, "
                    "status, prompt_version) VALUES (:id, :o, :v, :k, :h, CAST(:p AS jsonb), 'pending', :pv) "
                    "ON CONFLICT (version_id, kind, params_hash, prompt_version) DO NOTHING RETURNING id"
                ),
                {**key, "id": sid, "p": json.dumps(params)},
            )
        ).first()
        enqueue = inserted is not None
        row = (await db.execute(sel, key)).mappings().first()
    assert row is not None
    if row["status"] == "failed":  # a retry: the previous attempt failed
        reset = await db.execute(
            text(
                "UPDATE document_insights SET status = 'pending', content = NULL, created_at = now() "
                "WHERE id = :i AND owner_id = :o AND status = 'failed' RETURNING id"
            ),
            {"i": row["id"], "o": owner_id},
        )
        enqueue = reset.first() is not None
    elif row["status"] in ("pending", "running") and not enqueue:
        stale = await db.execute(  # an abandoned attempt: queue it again (the claim keeps this idempotent)
            text(
                "UPDATE document_insights SET status = 'pending', created_at = now() "
                "WHERE id = :i AND owner_id = :o AND status IN ('pending', 'running') "
                "AND created_at < now() - make_interval(secs => :s) RETURNING id"
            ),
            {"i": row["id"], "o": owner_id, "s": LEASE_S},
        )
        enqueue = stale.first() is not None
    await db.commit()
    row = (await db.execute(sel, key)).mappings().first()
    assert row is not None
    return Requested(summary_view(dict(row), document_id), row["status"] == "done", row["id"], enqueue)


SummaryEnqueue = Callable[[uuid.UUID], Awaitable[None]]


def get_summary_enqueue() -> SummaryEnqueue:
    """FastAPI dependency: production enqueuer (Celery `send_task` off the event loop); tests override it."""
    from app.worker.celery_app import celery_app

    async def enqueue(summary_id: uuid.UUID) -> None:
        await asyncio.to_thread(celery_app.send_task, SUMMARY_TASK, args=[str(summary_id)])

    return enqueue


async def mark_enqueue_failed(db: AsyncSession, owner_id: uuid.UUID, summary_id: uuid.UUID) -> None:
    """The task could not be queued: record it so the next POST retries instead of waiting out the lease."""
    await db.execute(
        text(
            "UPDATE document_insights SET status = 'failed', content = CAST(:c AS jsonb) "
            "WHERE id = :i AND owner_id = :o AND status = 'pending'"
        ),
        {"i": summary_id, "o": owner_id, "c": json.dumps({"error_code": "enqueue_failed"})},
    )
    await db.commit()


# --- worker side --------------------------------------------------------------------------------------------

SessionFactory = async_sessionmaker[AsyncSession]


def _error_code(e: BaseException) -> str:
    if isinstance(e, LlmError):
        return e.error_type
    return "no_chunks" if isinstance(e, NoChunksError) else "internal"


async def _exec(sm: SessionFactory, sql: str, params: Mapping[str, Any]) -> list[Any]:
    """One UPDATE ... RETURNING in its own short transaction; the rows it returns."""
    async with sm() as s:
        res = await s.execute(text(sql), dict(params))
        rows = list(res.fetchall())
        await s.commit()
    return rows


async def _finish(
    sm: SessionFactory,
    owner_id: uuid.UUID,
    summary_id: uuid.UUID,
    status: str,
    content: Mapping[str, Any],
    model: str | None = None,
) -> bool:
    """`running` -> `done` | `failed`. False when the claim was lost (a done row is never overwritten)."""
    rows = await _exec(
        sm,
        "UPDATE document_insights SET status = :st, content = CAST(:c AS jsonb), model = COALESCE(:m, model) "
        "WHERE id = :i AND owner_id = :o AND status = 'running' RETURNING id",
        {"i": summary_id, "o": owner_id, "st": status, "c": json.dumps(content), "m": model},
    )
    return bool(rows)


async def run_summary(
    sm: SessionFactory,
    gateway: LlmGateway,
    select: Select,
    summary_id: uuid.UUID,
    *,
    budget: int = SUMMARY_MAX_EVIDENCE_TOKENS,
) -> str:
    """Produce the summary of one `document_insights` row. Returns `done`, `failed`, `skipped` (already done,
    or another worker holds the claim) or `missing`. Idempotent under redelivery."""
    rows_claimed = await _exec(
        sm,
        "UPDATE document_insights SET status = 'running', created_at = now() WHERE id = :i AND kind = :k "
        "AND (status = 'pending' OR (status = 'running' AND created_at < now() - make_interval(secs => :s))) "
        "RETURNING owner_id, version_id, params",
        {"i": summary_id, "k": KIND, "s": LEASE_S},
    )
    claimed = rows_claimed[0] if rows_claimed else None
    if claimed is None:
        async with sm() as s:
            exists = (
                await s.execute(
                    text("SELECT 1 FROM document_insights WHERE id = :i AND kind = :k"),
                    {"i": summary_id, "k": KIND},
                )
            ).first()
        return "skipped" if exists else "missing"
    owner_id: uuid.UUID = claimed.owner_id
    version_id: uuid.UUID = claimed.version_id
    params: dict[str, Any] = claimed.params
    try:
        async with sm() as s:  # short read session, closed before any model or retrieval call
            doc = (
                await s.execute(
                    text(
                        "SELECT v.document_id AS document_id FROM document_versions v "
                        "JOIN documents d ON d.id = v.document_id AND d.owner_id = v.owner_id "
                        "WHERE v.id = :v AND v.owner_id = :o AND d.deleted_at IS NULL"
                    ),
                    {"v": version_id, "o": owner_id},
                )
            ).first()
            rows = (
                await s.execute(
                    text(
                        "SELECT id, ordinal, COALESCE(token_count, length(original_text) / 4) AS tokens "
                        "FROM chunks WHERE owner_id = :o AND version_id = :v ORDER BY ordinal"
                    ),
                    {"o": owner_id, "v": version_id},
                )
            ).all()
        if doc is None or not rows:
            raise NoChunksError("no live chunks")
        refs = [ChunkRef(r.id, int(r.ordinal), int(r.tokens or 0)) for r in rows]
        ranked: list[uuid.UUID] | None = None
        if sum(r.tokens for r in refs) > budget:
            try:
                ranked = list(
                    await select(owner_id, doc.document_id, version_id, params["focus"] or OVERVIEW_QUERY)
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:  # retrieval is best effort: the spread alone still gives a summary
                _log.warning(
                    "summary_retrieval_failed", summary_id=str(summary_id), error_type=type(e).__name__
                )
        plan = plan_evidence(refs, ranked, budget)

        async def load(ids: Sequence[uuid.UUID]) -> list[EvidenceChunk]:
            async with sm() as s:
                return await load_evidence_chunks(s, owner_id, ids)

        gen = await generate_summary(gateway, load, owner_id=owner_id, params=params, plan=plan)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        code = _error_code(e)
        _log.warning(
            "summary_failed", summary_id=str(summary_id), error_type=type(e).__name__, error_code=code
        )
        await _finish(sm, owner_id, summary_id, status="failed", content={"error_code": code})
        return "failed"
    await _finish(sm, owner_id, summary_id, status="done", content=content_of(gen), model=gen.model)
    _log.info(
        "summary_done", summary_id=str(summary_id), chunks=gen.evidence_chunks, cost_usd=str(gen.cost_usd)
    )
    return "done"


def retriever_select(retriever: Any, sm: SessionFactory) -> Select:
    """A `Select` over the production `Retriever`, scoped to one version of one document."""

    async def select(
        owner_id: uuid.UUID, document_id: uuid.UUID, version_id: uuid.UUID, query: str
    ) -> Sequence[uuid.UUID]:
        scope = Scope(entries=((document_id, version_id),))
        async with sm() as s:
            result = await retriever.retrieve(s, owner_id, scope, query)
        return list(result.selected)

    return select
