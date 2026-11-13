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
from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.chat.citations import Resolution, ResolvedSegment
from app.chat.graph import TurnState
from app.chat.nodes.generate import GenerateResult
from app.retrieval.query_embed import QueryEmbedding
from app.retrieval.retrieve import RetrievalResult, Retriever
from app.retrieval.scope import Scope

COMPARE_TOO_MANY = "COMPARE_TOO_MANY"
COMPARE_SIDE_MISSING = "COMPARE_SIDE_MISSING"
COMPARE_PROMPT: tuple[str, int] = ("answer_compare", 1)
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
) -> RetrievalResult:
    """One retrieval per scoped document (concurrent), each cut to its top `per_doc_passages(n)`.
    The result concatenates them in scope order, so the evidence is grouped by document."""
    per_doc = per_doc_passages(len(scope.entries))
    emb = embedding or await retriever.embed(query, owner_id)
    results = await asyncio.gather(
        *(
            retriever.retrieve(sm, owner_id, Scope(entries=(entry,)), query, embedding=emb)
            for entry in scope.entries
        )
    )
    order = {cid: i for i, cid in enumerate(c for r in results for c in r.selected[:per_doc])}
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


def missing_titles(state: TurnState) -> list[str]:
    """Titles of the scoped documents that no evidence chunk comes from."""
    have = {c.document_id for c in state.chunks}
    in_scope = set(state.scope.document_ids)
    return [d.title for d in state.docs if d.id in in_scope and d.id not in have]


def compare_call(state: TurnState) -> dict[str, Any]:
    """`generate` keyword arguments of a compare turn: route, prompt and the coverage note."""
    assert state.analysis is not None
    lines: list[str] = []
    if state.analysis.dimensions:
        lines.append("Dimensions to compare: " + "; ".join(state.analysis.dimensions) + ".")
    missing = missing_titles(state)
    if missing:
        lines.append("No evidence was found in: " + "; ".join(f"'{t}'" for t in missing) + ".")
    return {"route": "compare", "prompt": COMPARE_PROMPT, "coverage": "\n".join(lines) or None}


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
