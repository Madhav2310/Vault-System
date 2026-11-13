"""Contextual chunk headers, index side only (design §6.5; plan S17.05).

A short header situating a chunk in its document is added to the text that is embedded and lexically indexed:
`embedding_text = structural_prefix + "\\n" + contextual_prefix + "\\n" + original_text` (`tsv_en` follows:
it is generated from `embedding_text`). The header is stored in `chunks.contextual_prefix` only. It is never
part of `original_text` or `sentences`, never shown to the answer model and never cited (invariant I1): this
stage's SQL never names those columns.

Variants (`settings.contextualize_variant`, default `off`):
- `deterministic`: `"{document_type}: {first sentence of the brief summary} | Section: {section path}"`,
  no model call besides embeddings. Needs the document's `done` brief; without one the stage does nothing.
- `llm`: one `fast`-role call per chunk (prompt `chunk_context@1`) with the brief summary, the section
  path and the chunk; at most `MAX_CONTEXT_WORDS` words are kept. `contextualize_max_usd` caps a document's
  spend, counting what earlier deliveries of the same job already spent; chunks past the cap stay without one.

Only chunks whose `embedding_text` changes are re-embedded, so a second run makes no embedding call and an LLM
header that exists is never generated again. Nothing is written when anything fails (the old embeddings stay
valid and searchable); the stage never fails the pipeline. All writes happen in the runner's fenced commit,
each guarded by the `embedding_text` the compute step read, so a concurrent change is never overwritten.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.nodes_common import defuse
from app.core.logging import get_logger
from app.core.quota import over_quota
from app.ingestion.embed_index import EMBED_BATCH, vec_literal
from app.ingestion.errors import PermanentStageError
from app.ingestion.runner import StageContext, StageResult
from app.ingestion.sentences import split_sentences
from app.ingestion.stages import document_deleted
from app.insights.brief import EMPTY_PARAMS_HASH, KIND
from app.llm.errors import LlmError
from app.llm.gateway import LlmGateway

_log = get_logger("vault.ingestion.contextualize")

PROMPT: tuple[str, int] = ("chunk_context", 1)
PURPOSE = "contextualize"  # gateway node (routing.DEFAULT_ROUTES: role fast)
MAX_CONTEXT_WORDS = 50
MAX_SUMMARY_SENTENCE_CHARS = 300
MAX_CHUNK_CHARS = 6000
VARIANT_DETERMINISTIC = "deterministic"
VARIANT_LLM = "llm"
WAVE = 4  # concurrent header calls; the cost cap is checked before every wave


class ContextOut(BaseModel):
    """Strict-mode compatible: every field required, no defaults."""

    model_config = ConfigDict(extra="forbid")
    context: str


@dataclass(frozen=True)
class ChunkRow:
    id: uuid.UUID
    original_text: str
    structural_prefix: str
    contextual_prefix: str | None
    embedding_text: str
    section_path: tuple[str, ...]


@dataclass(frozen=True)
class BriefInfo:
    document_type: str
    summary: str


def section_label(path: Sequence[str]) -> str:
    return " > ".join(p for p in path if p) or "-"


def first_sentence(summary: str) -> str:
    s = " ".join(summary.split())
    spans = split_sentences(s)
    out = s[spans[0][0] : spans[0][1]].strip() if spans else s
    return out[:MAX_SUMMARY_SENTENCE_CHARS].rstrip()


def deterministic_prefix(brief: BriefInfo, section_path: Sequence[str]) -> str:
    return f"{brief.document_type}: {first_sentence(brief.summary)} | Section: {section_label(section_path)}"


def clip_context(raw: str) -> str:
    """One line of at most `MAX_CONTEXT_WORDS` words (the model's length instruction is not trusted)."""
    return " ".join(raw.split()[:MAX_CONTEXT_WORDS])


def build_embedding_text(structural_prefix: str, contextual_prefix: str | None, original_text: str) -> str:
    """Without a header this is exactly what the chunker produced (`prefix + "\\n" + text`)."""
    if not contextual_prefix:
        return f"{structural_prefix}\n{original_text}"
    return f"{structural_prefix}\n{contextual_prefix}\n{original_text}"


async def _read(
    ctx: StageContext,
) -> tuple[list[ChunkRow], BriefInfo | None, str, Decimal]:
    job = ctx.job
    async with ctx.sm() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT id, original_text, structural_prefix, contextual_prefix, embedding_text, "
                    "section_path FROM chunks WHERE owner_id = :o AND version_id = :v ORDER BY ordinal"
                ),
                {"o": job.owner_id, "v": job.version_id},
            )
        ).all()
        brief_row = (
            await s.execute(
                text(
                    "SELECT content FROM document_insights WHERE owner_id = :o AND version_id = :v "
                    "AND kind = :k AND params_hash = :h AND status = 'done' "
                    "ORDER BY prompt_version DESC LIMIT 1"
                ),
                {"o": job.owner_id, "v": job.version_id, "k": KIND, "h": EMPTY_PARAMS_HASH},
            )
        ).scalar_one_or_none()
        title = (
            await s.execute(
                text(
                    "SELECT d.title FROM documents d JOIN document_versions v ON v.document_id = d.id "
                    "WHERE v.id = :v AND v.owner_id = :o"
                ),
                {"o": job.owner_id, "v": job.version_id},
            )
        ).scalar_one_or_none()
        spent = (
            await s.execute(
                text(
                    "SELECT COALESCE(sum(cost_usd), 0) FROM llm_calls "
                    "WHERE job_id = :j AND purpose = :p AND owner_id = :o"
                ),
                {"j": job.id, "p": PURPOSE, "o": job.owner_id},
            )
        ).scalar_one()
    chunks = [
        ChunkRow(
            r.id,
            r.original_text,
            r.structural_prefix or "",
            r.contextual_prefix,
            r.embedding_text,
            tuple(r.section_path or ()),
        )
        for r in rows
    ]
    brief = None
    if isinstance(brief_row, dict) and brief_row.get("summary") and brief_row.get("document_type"):
        brief = BriefInfo(str(brief_row["document_type"]), str(brief_row["summary"]))
    return chunks, brief, title or "", Decimal(spent)


@dataclass
class Generated:
    headers: dict[uuid.UUID, str]
    calls: int = 0
    cost_usd: Decimal = Decimal(0)
    stopped: str | None = None  # budget | llm_error


async def generate_headers(
    gateway: LlmGateway,
    rows: Sequence[ChunkRow],
    *,
    brief: BriefInfo | None,
    title: str,
    owner_id: uuid.UUID,
    job_id: uuid.UUID,
    cap_usd: Decimal,
    already_spent: Decimal,
) -> Generated:
    """Headers for `rows` in waves of `WAVE` calls. The first wave is one call, which sets the per-call cost
    estimate; later waves are sized so that estimate times calls fits in the remaining cap."""
    out = Generated({})
    spent = already_spent
    estimate = Decimal(0)
    doc = {"title": defuse(title), "document_type": defuse(brief.document_type) if brief else None}
    summary = defuse(brief.summary) if brief else None

    async def one(r: ChunkRow) -> tuple[str, Decimal]:
        res = await gateway.call(
            purpose=PURPOSE,
            lane="background",
            prompt=PROMPT,
            owner_id=owner_id,
            job_id=job_id,
            schema=ContextOut,
            inputs={
                "document": {**doc, "summary": summary},
                "section": defuse(section_label(r.section_path)),
                "chunk": defuse(r.original_text[:MAX_CHUNK_CHARS]),
            },
        )
        assert res.output is not None
        return clip_context(res.output.context), res.cost_usd

    i = 0
    while i < len(rows):
        room = cap_usd - spent
        if room <= 0:
            out.stopped = "budget"
            break
        n = 1 if out.calls == 0 else WAVE if estimate == 0 else min(WAVE, int(room // estimate))
        if n < 1:
            out.stopped = "budget"
            break
        wave = rows[i : i + n]
        i += n
        results = await asyncio.gather(*(one(r) for r in wave), return_exceptions=True)
        failed = False
        for r, res in zip(wave, results, strict=True):
            if isinstance(res, asyncio.CancelledError):
                raise res
            if isinstance(res, LlmError):
                failed = True
                continue
            if isinstance(res, BaseException):
                raise res
            header, cost = res
            out.calls += 1
            out.cost_usd += cost
            spent += cost
            estimate = max(estimate, cost)
            if header:
                out.headers[r.id] = header
        if failed:
            out.stopped = "llm_error"
            break
    return out


async def embed_texts(
    gateway: LlmGateway,
    texts: Sequence[str],
    *,
    model: str,
    owner_id: uuid.UUID,
    job_id: uuid.UUID,
    concurrency: int,
) -> list[list[float]]:
    batches = [list(texts[i : i + EMBED_BATCH]) for i in range(0, len(texts), EMBED_BATCH)]
    sem = asyncio.Semaphore(max(1, concurrency))
    results: list[list[list[float]]] = [[] for _ in batches]

    async def run(i: int, batch: list[str]) -> None:
        async with sem:
            res = await gateway.embed(
                purpose="embed", lane="background", texts=batch, owner_id=owner_id, job_id=job_id, model=model
            )
            results[i] = res.vectors

    try:
        async with asyncio.TaskGroup() as tg:
            for i, b in enumerate(batches):
                tg.create_task(run(i, b))
    except ExceptionGroup as eg:
        raise eg.exceptions[0] from None
    return [v for r in results for v in r]


def _writer(
    ctx: StageContext,
    model: str,
    changes: Sequence[tuple[ChunkRow, str | None, str, list[float]]],
) -> Callable[[AsyncSession], Awaitable[None]]:
    job = ctx.job
    params = [
        {
            "p": prefix,
            "t": new_text,
            "e": vec_literal(vec),
            "m": model,
            "o": job.owner_id,
            "v": job.version_id,
            "id": row.id,
            "old": row.embedding_text,
        }
        for row, prefix, new_text, vec in changes
    ]

    async def write(s: AsyncSession) -> None:
        # original_text / sentences / spans / content_hash are deliberately absent from this statement.
        await s.execute(
            text(
                "UPDATE chunks SET contextual_prefix = :p, embedding_text = :t, "
                "embedding = CAST(:e AS halfvec), embedding_model = :m "
                "WHERE owner_id = :o AND version_id = :v AND id = :id AND embedding_text = :old"
            ),
            params,
        )

    return write


async def plan_and_embed(ctx: StageContext, variant: str, gateway: LlmGateway, settings: Any) -> StageResult:
    job = ctx.job
    chunks, brief, title, spent = await _read(ctx)
    detail: dict[str, Any] = {"variant": variant, "chunks": len(chunks)}
    if not chunks:
        return StageResult(detail={**detail, "changed": 0})
    headers: dict[uuid.UUID, str] = {}
    llm_calls, cost = 0, Decimal(0)
    if variant == VARIANT_DETERMINISTIC:
        if brief is None:
            return StageResult(detail={**detail, "skipped": "no_brief"})
        headers = {c.id: deterministic_prefix(brief, c.section_path) for c in chunks}
    else:
        headers = {c.id: c.contextual_prefix for c in chunks if c.contextual_prefix}
        todo = [c for c in chunks if not c.contextual_prefix]
        if todo:
            async with ctx.sm() as s:
                if await over_quota(s, job.owner_id):
                    return StageResult(detail={**detail, "skipped": "quota_deferred"})
            gen = await generate_headers(
                gateway,
                todo,
                brief=brief,
                title=title,
                owner_id=job.owner_id,
                job_id=job.id,
                cap_usd=Decimal(str(settings.contextualize_max_usd)),
                already_spent=spent,
            )
            headers |= gen.headers
            llm_calls, cost = gen.calls, gen.cost_usd
            if gen.stopped:
                detail["stopped"] = gen.stopped
    changed: list[tuple[ChunkRow, str | None, str]] = []
    for c in chunks:
        prefix = headers.get(c.id)
        new_text = build_embedding_text(c.structural_prefix, prefix, c.original_text)
        if new_text != c.embedding_text or prefix != c.contextual_prefix:
            changed.append((c, prefix, new_text))
    detail |= {"changed": len(changed), "llm_calls": llm_calls, "cost_usd": str(cost)}
    if not changed:
        return StageResult(detail=detail)
    model: str = settings.embedding_model
    vectors = await embed_texts(
        gateway,
        [t for _, _, t in changed],
        model=model,
        owner_id=job.owner_id,
        job_id=job.id,
        concurrency=settings.embed_concurrency,
    )
    if len(vectors) != len(changed):
        raise PermanentStageError("embedding_mismatch", "embedding count differs from the request")
    detail["embed_calls"] = -(-len(changed) // EMBED_BATCH)
    rows = [(c, p, t, v) for (c, p, t), v in zip(changed, vectors, strict=True)]
    return StageResult(write=_writer(ctx, model, rows), detail=detail)


class ContextualizeStage:
    name = "contextualize"

    async def run(self, ctx: StageContext) -> StageResult:
        settings = ctx.services["settings"]
        variant: str = settings.contextualize_variant
        if variant == "off":
            return StageResult(detail={"skipped": "off"})
        gateway: LlmGateway | None = ctx.services.get("gateway")
        if gateway is None:
            return StageResult(detail={"skipped": "no_gateway"})
        if await document_deleted(ctx):
            return StageResult(detail={"skipped": "deleted"})
        try:
            return await plan_and_embed(ctx, variant, gateway, settings)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # enrichment only: the version stays as it is, old embeddings stay valid
            code = e.error_type if isinstance(e, LlmError) else type(e).__name__
            _log.warning("contextualize_failed", job_id=str(ctx.job.id), error_type=code)
            return StageResult(detail={"variant": variant, "failed": code})
