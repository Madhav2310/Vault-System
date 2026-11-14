"""Compare across documents (design §8.3, S18.06): the 2-5 document limit, per-document retrieval and the
code-checked "not found" sentence.

With `intent=compare` and a scope of 2-5 documents the turn retrieves **per document** (each document keeps
its own top `max(3, 8 // n)` passages, so one document cannot crowd the others out) and generates with
`answer_compare@1`. More than 5 documents answers as plain question answering (`COMPARE_TOO_MANY`). A document
with no evidence is listed in the prompt's coverage tag and, when the model does not name it, code appends a
"nothing relevant was found" segment that names it (`COMPARE_SIDE_MISSING`). Plain code throughout: the
model never decides which documents are compared.
"""

import asyncio
import dataclasses
import uuid
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.chat.citations import Resolution, ResolvedSegment
from app.chat.graph import TurnState
from app.chat.nodes.generate import GenerateResult
from app.explain.templates import side_not_found_text
from app.llm.errors import LlmError
from app.llm.types import Usage
from app.retrieval.query_embed import QueryEmbedding
from app.retrieval.retrieve import RetrievalResult, Retriever
from app.retrieval.scope import Scope

COMPARE_TOO_MANY = "COMPARE_TOO_MANY"
COMPARE_SIDE_MISSING = "COMPARE_SIDE_MISSING"
COMPARE_SIDE_NOT_FOUND = "COMPARE_SIDE_NOT_FOUND"
COMPARE_SIDE_REPAIRED = "COMPARE_SIDE_REPAIRED"
COMPARE_PROMPT: tuple[str, int] = ("answer_compare", 1)
COMPARE_QUOTA_PROMPT: tuple[str, int] = ("answer_compare", 2)
QUOTA_CONCURRENCY = 3
MIN_DOCS = 2
MAX_DOCS = 5
TARGET_PASSAGES = 8


def per_doc_passages(n_documents: int) -> int:
    """`top_n_per_doc = max(3, 8 // n)`."""
    return max(3, TARGET_PASSAGES // max(1, n_documents))


def limit_compare(state: TurnState) -> None:
    """Keep `intent=compare` only for a scope of 2-5 documents (set `state.compare`); otherwise the turn is a
    plain question, with `COMPARE_TOO_MANY` when the scope is larger than the limit."""
    a = state.analysis
    if a is None or a.intent != "compare":
        return
    n = len(state.scope.entries)
    if MIN_DOCS <= n <= MAX_DOCS:
        state.compare = True
        return
    state.analysis = dataclasses.replace(a, intent="qa")
    if n > MAX_DOCS:
        state.reasons.append(COMPARE_TOO_MANY)


async def retrieve_per_doc(
    retriever: Retriever,
    sm: async_sessionmaker[AsyncSession],
    owner_id: uuid.UUID,
    scope: Scope,
    query: str,
    *,
    embedding: QueryEmbedding | None = None,
    quota: bool = False,
) -> RetrievalResult:
    """One retrieval per scoped document (concurrent), each cut to its top `per_doc_passages(n)`.
    The result concatenates them in scope order, so the evidence is grouped by document.

    `quota` (S22.05): concurrency is bounded and a document whose reranked selection is shorter than its
    quota is topped up from its own next-best candidates (chosen by code, never from another document)."""
    per_doc = per_doc_passages(len(scope.entries))
    emb = embedding or await retriever.embed(query, owner_id)
    sem = asyncio.Semaphore(QUOTA_CONCURRENCY if quota else max(1, len(scope.entries)))

    async def one(entry: tuple[uuid.UUID, uuid.UUID]) -> RetrievalResult:
        async with sem:
            return await retriever.retrieve(sm, owner_id, Scope(entries=(entry,)), query, embedding=emb)

    results = await asyncio.gather(*(one(entry) for entry in scope.entries))
    picks = [_quota_pick(r, per_doc) if quota else r.selected[:per_doc] for r in results]
    order = {cid: i for i, cid in enumerate(c for pick in picks for c in pick)}
    candidates = tuple(
        dataclasses.replace(c, selected=c.chunk_id in order, result_index=order.get(c.chunk_id))
        for r in results
        for c in r.candidates
    )
    timings: dict[str, int] = {}
    for r in results:
        for k, v in r.timings.items():
            timings[k] = max(timings.get(k, 0), v)  # the retrievals ran concurrently
    return RetrievalResult(
        candidates=candidates,
        selected=tuple(order),
        degraded=tuple(dict.fromkeys(d for r in results for d in r.degraded)),
        config_version=retriever.config.version,
        timings=timings,
    )


def _quota_pick(r: RetrievalResult, quota: int) -> tuple[uuid.UUID, ...]:
    """The document's selected chunks (up to `quota`), topped up from its best remaining candidates."""
    pick = list(r.selected[:quota])
    if len(pick) < quota:
        rest = sorted(
            (c for c in r.candidates if c.chunk_id not in pick),
            key=lambda c: (
                -(c.rerank_score if c.rerank_score is not None else float("-inf")),
                -(c.rrf_score or 0.0),
            ),
        )
        pick += [c.chunk_id for c in rest[: quota - len(pick)]]
    return tuple(pick)


def quota_on(settings: Any) -> bool:
    return bool(getattr(settings, "chat_compare_quota", False))


def missing_titles(state: TurnState) -> list[str]:
    """Titles of the scoped documents that no evidence chunk comes from."""
    have = {c.document_id for c in state.chunks}
    in_scope = set(state.scope.document_ids)
    return [d.title for d in state.docs if d.id in in_scope and d.id not in have]


def compare_prompt(settings: Any = None) -> tuple[str, int]:
    return COMPARE_QUOTA_PROMPT if quota_on(settings) else COMPARE_PROMPT


def entity_handles(state: TurnState) -> list[tuple[str, list[str]]]:
    """`(title, [E1, E2..])` per scoped document with evidence, in scope order (needs `state.evidence`)."""
    ev = state.evidence
    if ev is None:
        return []
    in_scope = set(state.scope.document_ids)
    out: list[tuple[str, list[str]]] = []
    for d in state.docs:
        hs = [ev.chunk_handles[c.chunk_id] for c in state.chunks if c.document_id == d.id]
        if d.id in in_scope and hs:
            out.append((d.title, hs))
    return out


def compare_call(state: TurnState, settings: Any = None) -> dict[str, Any]:
    """`generate` keyword arguments of a compare turn: route, prompt and the coverage note. With the quota
    switch the note also groups the evidence handles by document and the prompt is `answer_compare@2`."""
    assert state.analysis is not None
    lines: list[str] = []
    if state.analysis.dimensions:
        lines.append("Dimensions to compare: " + "; ".join(state.analysis.dimensions) + ".")
    missing = missing_titles(state)
    if missing:
        lines.append("No evidence was found in: " + "; ".join(f"'{t}'" for t in missing) + ".")
    if quota_on(settings):
        groups = entity_handles(state)
        if groups:
            listing = "; ".join(f"'{t}' = {', '.join(h)}" for t, h in groups)
            lines.append(f"Evidence by document (each must get a cited segment): {listing}.")
    return {"route": "compare", "prompt": compare_prompt(settings), "coverage": "\n".join(lines) or None}


def name_missing_sides(gen: GenerateResult, titles: Sequence[str]) -> GenerateResult:
    """Code check: every document without evidence must be named in the answer. A missing one is added as a
    code-written, uncited segment."""
    if gen.response_type != "answer" or not titles:
        return gen
    text = gen.text.lower()
    absent = [t for t in titles if t.lower() not in text]
    if not absent:
        return gen
    segments = list(gen.resolution.segments)
    for t in absent:
        segments.append(ResolvedSegment(len(segments), f"Nothing relevant was found in {t}.", "none", ()))
    resolution = Resolution(tuple(segments), gen.resolution.reasons, gen.resolution.unresolved_count)
    return dataclasses.replace(gen, resolution=resolution, reasons=(*gen.reasons, COMPARE_SIDE_MISSING))


Repair = Callable[[uuid.UUID, str], Awaitable[GenerateResult | None]]


async def coverage_gate(state: TurnState, gen: GenerateResult, repair: Repair) -> GenerateResult:
    """Code check (S22.05): every scoped document has a resolved citation in the answer.

    A document without evidence gets the template statement and no model call. A document with evidence but
    no resolved citation gets exactly one targeted `repair(document_id, title)` call (a single-document
    generation); when that still cites nothing from the document, it gets the template statement too.
    Plain code: the model never decides which documents are compared, and nothing loops."""
    if gen.response_type != "answer":
        return gen
    doc_of = {c.chunk_id: c.document_id for c in state.chunks}
    cited = {doc_of.get(r.chunk_id) for s in gen.resolution.segments for r in s.references}
    have = set(doc_of.values())
    titles = {d.id: d.title for d in state.docs}
    segments = list(gen.resolution.segments)
    reasons = list(gen.reasons)
    usage, cost, calls = gen.usage, gen.cost_usd, gen.call_ids
    for doc_id in state.scope.document_ids:
        if doc_id in cited:
            continue
        title = titles.get(doc_id, "this document")
        fixed: list[ResolvedSegment] = []
        if doc_id in have:
            try:
                out = await repair(doc_id, title)
            except LlmError:
                out = None
            if out is not None:
                usage, cost, calls = _add(usage, out.usage), cost + out.cost_usd, (*calls, *out.call_ids)
                fixed = [
                    s for s in out.resolution.segments
                    if s.references and all(doc_of.get(r.chunk_id) == doc_id for r in s.references)
                ]  # fmt: skip
        if fixed:
            segments += [dataclasses.replace(s, ordinal=len(segments) + i) for i, s in enumerate(fixed)]
            reasons.append(COMPARE_SIDE_REPAIRED)
        else:
            segments.append(ResolvedSegment(len(segments), side_not_found_text(title), "none", ()))
            reasons.append(COMPARE_SIDE_NOT_FOUND)
    if len(segments) == len(gen.resolution.segments):
        return gen
    resolution = dataclasses.replace(gen.resolution, segments=tuple(segments))
    return dataclasses.replace(
        gen,
        resolution=resolution,
        reasons=tuple(dict.fromkeys(reasons)),
        usage=usage,
        cost_usd=cost,
        call_ids=calls,
    )


def _add(a: Usage, b: Usage) -> Usage:
    return Usage(
        a.input_tokens + b.input_tokens,
        a.output_tokens + b.output_tokens,
        a.cache_read_tokens + b.cache_read_tokens,
        a.cache_write_tokens + b.cache_write_tokens,
    )
