"""Brief stage (design §6.2-6.3, §9.1): the last ingestion stage, `ai` queue.

- One `brief_extract` call when the version's chunks fit `BRIEF_MAX_EVIDENCE_TOKENS`; otherwise map over
  groups of whole sections (same budget each), then one `brief_merge` call. The merge model sees the map
  outputs as plain JSON (never document text) and returns only the document-level fields: facts are united
  by code, so their citations always come from the map step.
- `BRIEF_MAX_USD` caps a document's spend: when it is reached no further map call starts, what exists is
  merged and the brief is marked partial (`BRIEF_PARTIAL_BUDGET`).
- The brief is an enrichment. Any failure here is recorded on the `document_insights` row and never fails the
  document: the version stays `ready` and the job completes. A success moves `ready` -> `enriched` and writes
  `documents.category` / `documents.tags`.
- Idempotent: a `done` row for the same (version, kind, params, prompt version) means the stage does nothing,
  and the write never overwrites one. A `failed` row is retried by the next run.
- No DB connection is held across a model call; document text is never logged.
"""

import asyncio
import json
import uuid
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.evidence import EvidenceChunk, build_evidence
from app.core.ids import uuid7
from app.core.logging import get_logger
from app.core.quota import over_quota
from app.ingestion import status as vs
from app.ingestion.runner import StageContext, StageResult
from app.ingestion.stages import document_deleted
from app.insights.brief import (
    BRIEF_FACTS_CAPPED,
    BRIEF_MERGE_FALLBACK,
    BRIEF_PARTIAL_BUDGET,
    EMPTY_PARAMS_HASH,
    KIND,
    MAX_FACTS,
    MAX_TONE_EVIDENCE,
    PROMPT_VERSION,
    Brief,
    BriefOut,
    MergeOut,
    Normalized,
    ResolvedFact,
    StoredEvidence,
    StoredFact,
    StoredTone,
    brief_schema,
    dedupe_facts,
    normalize,
    resolve_facts,
    resolve_tone,
)
from app.insights.verify import verify_fact, verify_tone
from app.llm.errors import LlmError
from app.llm.gateway import LlmGateway
from app.retrieval.load import load_evidence_chunks

EXTRACT_PROMPT: tuple[str, int] = ("brief_extract", PROMPT_VERSION)
MERGE_PROMPT: tuple[str, int] = ("brief_merge", PROMPT_VERSION)
MAX_MERGE_FACTS_PER_PART = 60
MAX_MERGE_VALUE_CHARS = 200

_log = get_logger("vault.insights.brief")


# --- grouping ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ChunkRef:
    id: uuid.UUID
    tokens: int
    section: tuple[str, ...]  # the chunk's section path


def plan_groups(chunks: Sequence[ChunkRef], budget: int) -> list[list[uuid.UUID]]:
    """Consecutive top-level sections packed into groups of at most `budget` tokens. A section larger than the
    budget is split between chunks (a single chunk larger than the budget gets a group of its own). When
    everything fits there is exactly one group."""
    sections: list[list[ChunkRef]] = []
    for c in chunks:
        if sections and sections[-1][0].section[:1] == c.section[:1]:
            sections[-1].append(c)
        else:
            sections.append([c])
    groups: list[list[ChunkRef]] = []
    cur: list[ChunkRef] = []
    cur_t = 0
    for sec in sections:
        t = sum(c.tokens for c in sec)
        if t > budget:
            if cur:
                groups.append(cur)
            piece: list[ChunkRef] = []
            pt = 0
            for c in sec:
                if piece and pt + c.tokens > budget:
                    groups.append(piece)
                    piece, pt = [], 0
                piece.append(c)
                pt += c.tokens
            cur, cur_t = piece, pt  # the remainder can still share a group with the next section
        elif cur and cur_t + t > budget:
            groups.append(cur)
            cur, cur_t = list(sec), t
        else:
            cur += sec
            cur_t += t
    if cur:
        groups.append(cur)
    return [[c.id for c in g] for g in groups]


# --- generation -------------------------------------------------------------------------------------------


LoadChunks = Callable[[Sequence[uuid.UUID]], Awaitable[list[EvidenceChunk]]]


class NoChunksError(Exception):
    pass


@dataclass(frozen=True)
class Part:
    fields: Normalized
    facts: tuple[ResolvedFact, ...]
    tone_evidence: tuple[StoredEvidence, ...] = ()


@dataclass(frozen=True)
class Generated:
    brief: Brief
    model: str
    cost_usd: Decimal
    calls: int


def _merge_inputs(parts: Sequence[Part]) -> dict[str, Any]:
    return {
        "parts": [
            {
                "document_type": p.fields.document_type,
                "summary": p.fields.summary,
                "category": p.fields.category,
                "tags": list(p.fields.tags),
                "suggested_questions": list(p.fields.suggested_questions),
                "tone": p.fields.tone,
                "tone_note": p.fields.tone_note,
                "facts": [
                    {"field": f.field, "value": f.value[:MAX_MERGE_VALUE_CHARS]}
                    for f in p.facts[:MAX_MERGE_FACTS_PER_PART]
                ],
            }
            for p in parts
        ]
    }


def _fallback_fields(parts: Sequence[Part]) -> Normalized:
    """Deterministic document-level fields when the merge call fails: the first part's summary and type, the
    commonest category, and the tags and questions of all parts in order."""
    first = parts[0].fields
    category = Counter(p.fields.category for p in parts).most_common(1)[0][0]
    tags = [t for p in parts for t in p.fields.tags]
    questions = [q for p in parts for q in p.fields.suggested_questions]
    tone = Counter(p.fields.tone for p in parts).most_common(1)[0][0]
    note = next(p.fields.tone_note for p in parts if p.fields.tone == tone)
    out = BriefOut(
        tone=tone,  # type: ignore[arg-type]
        tone_note=note,
        tone_evidence=[],
        document_type=first.document_type,
        summary=first.summary,
        category=category,  # type: ignore[arg-type]
        tags=tags,
        suggested_questions=questions,
        facts=[],
    )
    return normalize(out)


def _finalize(
    fields: Normalized,
    facts: Sequence[ResolvedFact],
    version_id: uuid.UUID,
    reasons: list[str],
    partial: bool,
    tone_evidence: Sequence[StoredEvidence] = (),
) -> Brief:
    tone_verdict = verify_tone(tone_evidence, version_id)
    united = dedupe_facts(facts)
    if len(united) > MAX_FACTS:
        united = united[:MAX_FACTS]
        reasons.append(BRIEF_FACTS_CAPPED)
    stored: list[StoredFact] = []
    for f in united:
        verdict = verify_fact(f.field, f.value, f.evidence, version_id)
        stored.append(
            StoredFact(
                field=f.field,
                value=f.value,
                status=verdict.status,
                reason=verdict.reason,
                evidence=list(f.evidence),
            )
        )
    return Brief(
        document_type=fields.document_type,
        category=fields.category,
        tags=list(fields.tags),
        summary=fields.summary,
        suggested_questions=list(fields.suggested_questions),
        facts=stored,
        reasons=list(dict.fromkeys(reasons)),
        partial=partial,
        tone=StoredTone(
            label=fields.tone,
            note=fields.tone_note,
            status=tone_verdict.status,
            reason=tone_verdict.reason,
            evidence=list(tone_evidence),
        ),
    )


def _merged_tone_evidence(parts: Sequence[Part], label: str) -> list[StoredEvidence]:
    """Tone citations of the merged brief come from the map step, never from the merge model: the parts that
    chose the merged label first, then the others; one per sentence range; at most `MAX_TONE_EVIDENCE`."""
    ordered = [p for p in parts if p.fields.tone == label] + [p for p in parts if p.fields.tone != label]
    out: list[StoredEvidence] = []
    seen: set[tuple[uuid.UUID, int, int]] = set()
    for p in ordered:
        for e in p.tone_evidence:
            key = (e.chunk_id, e.sentence_start, e.sentence_end)
            if key not in seen:
                seen.add(key)
                out.append(e)
    return out[:MAX_TONE_EVIDENCE]


async def generate_brief(
    gateway: LlmGateway,
    load: LoadChunks,
    *,
    owner_id: uuid.UUID,
    version_id: uuid.UUID,
    job_id: uuid.UUID | None,
    groups: Sequence[Sequence[uuid.UUID]],
    max_usd: Decimal,
    concurrency: int = 1,
) -> Generated:
    """Extract per group (sequential, so the cost cap is exact), then merge. `load` returns a group's chunks
    (the stage reads them in a short session). Raises `LlmError` (or the underlying error) when an extract
    call fails; the stage turns that into a failed row."""
    reasons: list[str] = []
    spent = Decimal(0)
    reserved = Decimal(0)  # estimated cost of calls in flight
    calls = 0
    model = ""
    partial = False
    estimate = Decimal(0)  # per-call cost estimate: the largest actual cost seen so far
    slots: list[tuple[Part, list[str], str] | None] = [None] * len(groups)
    next_group = 0
    first_done = asyncio.Event()

    async def run_group(i: int, ids: Sequence[uuid.UUID]) -> None:
        nonlocal spent, reserved, calls, model, estimate
        evidence = build_evidence(await load(list(ids)))
        res = await gateway.call(
            purpose="brief_extract",
            lane="background",
            prompt=EXTRACT_PROMPT,
            owner_id=owner_id,
            schema=brief_schema(evidence),
            messages=[{"role": "user", "content": evidence.text}],
            job_id=job_id,
        )
        assert res.output is not None
        calls += 1
        spent += res.cost_usd
        estimate = max(estimate, res.cost_usd)
        model = model or res.model
        fields = normalize(res.output)
        resolved, resolve_reasons = resolve_facts(fields.facts, evidence)
        tone_refs, tone_reasons = resolve_tone(fields.tone_note, fields.tone_evidence, evidence)
        slots[i] = (
            Part(fields, tuple(resolved), tone_refs),
            [*fields.reasons, *resolve_reasons, *tone_reasons],
            res.model,
        )

    async def worker() -> None:
        nonlocal next_group, reserved, partial
        while next_group < len(groups):
            i = next_group
            next_group += 1  # claim the group before any await: two workers must never take the same one
            if i > 0:
                await first_done.wait()  # the first call sets the cost estimate used for reservations
                if spent + reserved >= max_usd:  # in-flight calls count at the largest cost seen
                    partial = True
                    next_group = len(groups)  # nobody starts later groups either
                    return
            reserve = estimate
            reserved += reserve
            try:
                await run_group(i, groups[i])
            finally:
                reserved -= reserve
                if i == 0:
                    first_done.set()

    try:
        async with asyncio.TaskGroup() as tg:
            for _ in range(max(1, min(concurrency, len(groups)))):
                tg.create_task(worker())
    except ExceptionGroup as eg:
        raise eg.exceptions[0] from None
    if partial:
        reasons.append(BRIEF_PARTIAL_BUDGET)
    parts: list[Part] = []
    for slot in slots:  # merged in group order, never completion order
        if slot is not None:
            parts.append(slot[0])
            reasons += slot[1]
    if not parts:
        raise NoChunksError("the version has no chunks to brief")
    all_facts = [f for p in parts for f in p.facts]
    if len(parts) == 1:
        return Generated(
            _finalize(parts[0].fields, all_facts, version_id, reasons, partial, parts[0].tone_evidence),
            model,
            spent,
            calls,
        )
    try:
        res_m = await gateway.call(
            purpose="brief_merge",
            lane="background",
            prompt=MERGE_PROMPT,
            owner_id=owner_id,
            schema=MergeOut,
            inputs=_merge_inputs(parts),
            job_id=job_id,
        )
        assert res_m.output is not None
        calls += 1
        spent += res_m.cost_usd
        fields = normalize(BriefOut(**res_m.output.model_dump(), facts=[], tone_evidence=[]))
    except LlmError:
        reasons.append(BRIEF_MERGE_FALLBACK)
        fields = _fallback_fields(parts)
    tone_refs = _merged_tone_evidence(parts, fields.tone)
    return Generated(
        _finalize(fields, all_facts, version_id, reasons, partial, tone_refs), model, spent, calls
    )


# --- persistence ------------------------------------------------------------------------------------------

_UPSERT = (
    "INSERT INTO document_insights (id, owner_id, version_id, kind, params_hash, params, content, status, "
    "model, prompt_version) VALUES (:id, :o, :v, :kind, :h, CAST('{}' AS jsonb), CAST(:c AS jsonb), :st, "
    ":m, :pv) ON CONFLICT (version_id, kind, params_hash, prompt_version) DO UPDATE SET "
    "content = EXCLUDED.content, status = EXCLUDED.status, model = EXCLUDED.model "
    "WHERE document_insights.status <> 'done' RETURNING id"
)


async def _upsert(
    s: AsyncSession,
    owner_id: uuid.UUID,
    version_id: uuid.UUID,
    content: Mapping[str, Any],
    status: str,
    model: str | None,
) -> bool:
    row = (
        await s.execute(
            text(_UPSERT),
            {
                "id": uuid7(),
                "o": owner_id,
                "v": version_id,
                "kind": KIND,
                "h": EMPTY_PARAMS_HASH,
                "c": json.dumps(content),
                "st": status,
                "m": model,
                "pv": PROMPT_VERSION,
            },
        )
    ).first()
    return row is not None


def _error_code(e: BaseException) -> str:
    if isinstance(e, LlmError):
        return e.error_type
    return "no_chunks" if isinstance(e, NoChunksError) else "internal"


class BriefStage:
    name = "brief"

    async def run(self, ctx: StageContext) -> StageResult:
        gateway: LlmGateway | None = ctx.services.get("gateway")
        if gateway is None:  # no model access in this runtime: leave the version `ready`
            return StageResult(detail={"skipped": "no_gateway"})
        if await document_deleted(ctx):
            return StageResult(detail={"skipped": "deleted"})
        settings = ctx.services["settings"]
        job = ctx.job
        async with ctx.sm() as s:
            existing = (
                await s.execute(
                    text(
                        "SELECT status FROM document_insights WHERE owner_id = :o AND version_id = :v "
                        "AND kind = :k AND params_hash = :h AND prompt_version = :pv"
                    ),
                    {
                        "o": job.owner_id,
                        "v": job.version_id,
                        "k": KIND,
                        "h": EMPTY_PARAMS_HASH,
                        "pv": PROMPT_VERSION,
                    },
                )
            ).scalar_one_or_none()
            rows = (
                await s.execute(
                    text(
                        "SELECT id, token_count, section_path FROM chunks "
                        "WHERE owner_id = :o AND version_id = :v ORDER BY ordinal"
                    ),
                    {"o": job.owner_id, "v": job.version_id},
                )
            ).all()
        if existing == "done":
            return StageResult(detail={"skipped": "exists"})
        async with ctx.sm() as s:
            deferred = await over_quota(s, job.owner_id)
        if deferred:  # over the daily quota: defer, never fail; the version stays as it is
            _log.info("brief_quota_deferred", job_id=str(job.id))
            return StageResult(write=self._deferred_writer(ctx), detail={"brief": "quota_deferred"})
        refs = [ChunkRef(r.id, int(r.token_count or 0), tuple(r.section_path or ())) for r in rows]
        groups = plan_groups(refs, settings.brief_max_evidence_tokens)
        try:

            async def load(ids: Sequence[uuid.UUID]) -> list[EvidenceChunk]:
                async with ctx.sm() as s:  # a short read session, closed before the model call
                    return await load_evidence_chunks(s, job.owner_id, ids)

            gen = await generate_brief(
                gateway,
                load,
                owner_id=job.owner_id,
                version_id=job.version_id,
                job_id=job.id,
                groups=groups,
                max_usd=Decimal(str(settings.brief_max_usd)),
                concurrency=settings.brief_concurrency,
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            code = _error_code(e)
            _log.warning("brief_failed", job_id=str(job.id), error_type=type(e).__name__, error_code=code)
            return StageResult(write=self._failed_writer(ctx, code), detail={"brief": "failed", "code": code})
        return StageResult(
            write=self._success_writer(ctx, gen),
            detail={
                "brief": "partial" if gen.brief.partial else "done",
                "groups": len(groups),
                "calls": gen.calls,
                "facts": len(gen.brief.facts),
                "verified": sum(f.status == "verified" for f in gen.brief.facts),
                "cost_usd": str(gen.cost_usd),
            },
        )

    @staticmethod
    def _deferred_writer(ctx: StageContext) -> Any:
        job = ctx.job

        async def write(s: AsyncSession) -> None:
            content = {"reason": "quota_exceeded"}
            await _upsert(s, job.owner_id, job.version_id, content, "quota_deferred", None)

        return write

    @staticmethod
    def _failed_writer(ctx: StageContext, code: str) -> Any:
        job = ctx.job

        async def write(s: AsyncSession) -> None:
            await _upsert(s, job.owner_id, job.version_id, {"error_code": code}, "failed", None)

        return write

    @staticmethod
    def _success_writer(ctx: StageContext, gen: Generated) -> Any:
        job = ctx.job
        brief = gen.brief

        async def write(s: AsyncSession) -> None:
            if not await _upsert(
                s, job.owner_id, job.version_id, brief.model_dump(mode="json"), "done", gen.model
            ):
                return  # a concurrent delivery already stored it
            await s.execute(
                text(
                    "UPDATE documents SET category = :c, tags = :t "
                    "WHERE owner_id = :o AND current_version_id = :v"
                ),
                {"c": brief.category, "t": brief.tags, "o": job.owner_id, "v": job.version_id},
            )
            current = (
                await s.execute(
                    text("SELECT status FROM document_versions WHERE id = :v AND owner_id = :o FOR UPDATE"),
                    {"v": job.version_id, "o": job.owner_id},
                )
            ).scalar_one()
            if current == vs.READY:  # a version superseded meanwhile keeps its status
                await vs.set_version_status(s, job.version_id, vs.ENRICHED)

        return write
