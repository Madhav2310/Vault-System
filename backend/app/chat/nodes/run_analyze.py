"""`analyze` node: the analysis call and the query embedding of the raw question run at the same moment.

The embedding is speculative: `retrieve` reuses it when the standalone query equals the question after
`normalize_query`, otherwise it embeds again (cached by key). An embedding failure never fails the node.
"""

import asyncio

from app.chat.graph import TurnCtx, TurnState
from app.chat.harness import ROUTE_LIMITS, TurnBudget
from app.chat.nodes._common import TARGET_IGNORED
from app.chat.nodes.analyze import analyze_query
from app.retrieval.query_embed import QueryEmbedding
from app.retrieval.scope import ScopeWideningError, narrow


async def analyze(state: TurnState, ctx: TurnCtx) -> TurnState:
    deps = ctx.deps

    async def embed() -> QueryEmbedding | None:
        try:
            return await deps.retriever.embed(state.question, ctx.owner_id)
        except Exception:  # speculative: retrieve embeds again if this one is missing
            return None

    # tasks start in this order, so the scripted FakeProvider queue sees the analysis call first
    a_task = asyncio.create_task(
        analyze_query(
            deps.gateway,
            question=state.question,
            memory=state.memory,
            documents=state.docs,
            owner_id=ctx.owner_id,
            run_id=ctx.run_id,
            budget=ctx.budget,
        )
    )
    e_task = asyncio.create_task(embed())
    try:
        analysis = await a_task
    except BaseException:
        e_task.cancel()
        raise
    state.embedding = await e_task
    state.analysis = analysis
    state.degraded += analysis.degraded
    state.call_ids += analysis.call_ids
    state.route = analysis.intent if analysis.intent in ROUTE_LIMITS else "qa"
    ctx.budget = TurnBudget(ROUTE_LIMITS[state.route], ctx.budget.deadline_at, ctx.budget.calls_used)
    try:
        state.scope = narrow(state.scope, analysis.target_document_ids)
    except ScopeWideningError:  # never widen; keep the wider scope but leave a trace
        state.reasons.append(TARGET_IGNORED)
    state.last_decision = {"intent": analysis.intent}
    return state
