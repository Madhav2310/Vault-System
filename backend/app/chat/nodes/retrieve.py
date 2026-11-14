"""`retrieve` node: hybrid retrieval on short sessions, then the evidence chunks of the selection.

Reuses the speculative query embedding (started while `analyze` ran) when the standalone query is the
question itself after `normalize_query`; otherwise the retriever embeds the standalone query (cached by key).
When `analyze` ran the retrieval of the raw question speculatively and the query and scope are unchanged, its
result is used as is (`speculative: hit`); an unusable one is dropped (`miss`) and retrieval runs here.
"""

import uuid

from app.chat.clarify import near_tie
from app.chat.graph import TurnCtx, TurnState
from app.chat.nodes.run_analyze import clarify_ok
from app.chat.speculative import HIT, MISS, cancel
from app.retrieval.load import load_evidence_chunks
from app.retrieval.query_embed import normalize_query
from app.retrieval.retrieve import RetrievalResult


def _near_tie(state: TurnState, ctx: TurnCtx, result: RetrievalResult) -> None:
    """Clarification rule (a): the selected evidence spans several documents with near-equal scores."""
    a = state.analysis
    if not state.clarify_hint or state.compare or a is None or not clarify_ok(state, ctx):
        return
    meta = {c.chunk_id: c for c in state.chunks if c.document_id is not None}
    sel = [c for c in result.candidates if c.selected and c.expanded_from is None and c.chunk_id in meta]
    use_rerank = bool(sel) and all(c.rerank_score is not None for c in sel)
    scored: dict[uuid.UUID, float] = {}
    docs: dict[uuid.UUID, tuple[str, int | None]] = {}
    for c in sel:
        score = c.rerank_score if use_rerank else c.rrf_score
        ch = meta[c.chunk_id]
        if ch.document_id is None or score is None:
            continue
        scored[ch.document_id] = max(score, scored.get(ch.document_id, score))
        docs[ch.document_id] = (ch.doc_title, ch.version_no)
    narrowed = (
        bool(a.target_document_ids) or state.pick_document_id is not None or len(state.scope.entries) < 2
    )
    state.clarification = near_tie(scored, docs, narrowed=narrowed, hinted=True)


async def retrieve(state: TurnState, ctx: TurnCtx) -> TurnState:
    assert state.analysis is not None
    query = state.analysis.standalone_query
    emb = state.embedding if normalize_query(query) == normalize_query(state.question) else None
    deps = ctx.deps
    spec, state.spec_retrieval = state.spec_retrieval, None
    result = None
    if spec is not None:
        try:
            if (
                not spec.task.cancelled()
                and normalize_query(query) == normalize_query(spec.query)
                and state.scope == spec.scope
            ):
                result = await spec.task
        except Exception:  # the speculative run failed: retrieve again, as if it never ran
            result = None
        finally:
            await cancel(spec.task)  # no-op when it finished
    speculative = "none" if spec is None else (HIT if result is not None else MISS)
    if result is None:
        result = await deps.retriever.retrieve(
            deps.sm, ctx.owner_id, state.scope, query, embedding=emb, run_id=ctx.run_id
        )
    async with deps.sm() as s:
        state.chunks = await load_evidence_chunks(s, ctx.owner_id, list(result.selected))
    state.retrieval = result
    _near_tie(state, ctx, result)
    state.degraded += result.degraded
    state.last_decision = {
        "selected": len(state.chunks),
        "degraded": list(result.degraded),
        "embed_reused": emb is not None,
        "cache_hit": result.cache_hit,
        "speculative": speculative,
        **result.timings,
    }
    return state
