"""`retrieve` node: hybrid retrieval on short sessions, then the evidence chunks of the selection.

Reuses the speculative query embedding (started while `analyze` ran) when the standalone query is the
question itself after `normalize_query`; otherwise the retriever embeds the standalone query (cached by key).
"""

from app.chat.graph import TurnCtx, TurnState
from app.retrieval.load import load_evidence_chunks
from app.retrieval.query_embed import normalize_query


async def retrieve(state: TurnState, ctx: TurnCtx) -> TurnState:
    assert state.analysis is not None
    query = state.analysis.standalone_query
    emb = state.embedding if normalize_query(query) == normalize_query(state.question) else None
    deps = ctx.deps
    result = await deps.retriever.retrieve(
        deps.sm, ctx.owner_id, state.scope, query, embedding=emb, run_id=ctx.run_id
    )
    async with deps.sm() as s:
        state.chunks = await load_evidence_chunks(s, ctx.owner_id, list(result.selected))
    state.retrieval = result
    state.degraded += result.degraded
    state.last_decision = {
        "selected": len(state.chunks),
        "degraded": list(result.degraded),
        "embed_reused": emb is not None,
        "cache_hit": result.cache_hit,
        **result.timings,
    }
    return state
