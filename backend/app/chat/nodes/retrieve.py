"""`retrieve` node: hybrid retrieval on short sessions, then the evidence chunks of the selection.

Reuses the speculative query embedding (started while `analyze` ran) when the standalone query is the
question itself after `normalize_query`; otherwise the retriever embeds the standalone query (cached by key).
When `analyze` ran the retrieval of the raw question speculatively and the query and scope are unchanged, its
result is used as is (`speculative: hit`); an unusable one is dropped (`miss`) and retrieval runs here.
"""

from app.chat.graph import TurnCtx, TurnState
from app.chat.speculative import HIT, MISS, cancel
from app.retrieval.load import load_evidence_chunks
from app.retrieval.query_embed import normalize_query


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
