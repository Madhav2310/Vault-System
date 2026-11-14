"""`compare_retrieve` node (S18.06): per-document retrieval for a compare turn."""

from app.chat.compare import quota_on, retrieve_per_doc
from app.chat.graph import TurnCtx, TurnState
from app.retrieval.load import load_evidence_chunks
from app.retrieval.query_embed import normalize_query


async def compare_retrieve(state: TurnState, ctx: TurnCtx) -> TurnState:
    assert state.analysis is not None
    query = state.analysis.standalone_query
    emb = state.embedding if normalize_query(query) == normalize_query(state.question) else None
    deps = ctx.deps
    result = await retrieve_per_doc(
        deps.retriever, deps.sm, ctx.owner_id, state.scope, query,
        embedding=emb, quota=quota_on(deps.gateway.settings),
    )  # fmt: skip
    async with deps.sm() as s:
        state.chunks = await load_evidence_chunks(s, ctx.owner_id, list(result.selected))
    state.retrieval = result
    state.degraded += result.degraded
    per_doc: dict[str, int] = {}
    for c in state.chunks:
        key = str(c.document_id)
        per_doc[key] = per_doc.get(key, 0) + 1
    state.last_decision = {
        "documents": len(state.scope.entries),
        "selected": len(state.chunks),
        "per_document": sorted(per_doc.values(), reverse=True),
        "degraded": list(result.degraded),
        **result.timings,
    }
    return state
