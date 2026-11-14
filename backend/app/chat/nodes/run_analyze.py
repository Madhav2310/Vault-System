"""`analyze` node: the analysis call and the query embedding of the raw question run at the same moment.

The embedding is speculative: `retrieve` reuses it when the standalone query equals the question after
`normalize_query`, otherwise it embeds again (cached by key). An embedding failure never fails the node.
Retrieval of the raw question runs speculatively too (`SpeculativeRetrieval`): it is kept only when the
analysis leaves the query and the scope unchanged and the turn goes on to plain retrieval, else cancelled.
It costs at most the one embedding (the LLM reranker config skips it: that rerank would be a paid call).
"""

import asyncio

from app.chat.compare import limit_compare
from app.chat.graph import TurnCtx, TurnState
from app.chat.harness import ROUTE_LIMITS, TurnBudget
from app.chat.nodes._common import TARGET_IGNORED
from app.chat.nodes.analyze import AnalyzeResult, analyze_query
from app.chat.routing_rules import FAST_PATH
from app.chat.speculative import SpeculativeRetrieval, cancel
from app.retrieval.query_embed import QueryEmbedding, normalize_query
from app.retrieval.retrieve import RetrievalResult
from app.retrieval.scope import ScopeWideningError, narrow


async def analyze(state: TurnState, ctx: TurnCtx) -> TurnState:
    if state.fast_path:  # S17.10: the raw question is the standalone query, route `qa`, no model call
        query = " ".join(state.question.split())
        state.analysis = AnalyzeResult("qa", query, (), (), False, None, (FAST_PATH,))
        state.last_status, state.last_reason = "skipped", FAST_PATH
        state.last_decision = {"intent": "qa"}
        return state
    deps = ctx.deps

    async def embed() -> QueryEmbedding | None:
        try:
            return await deps.retriever.embed(state.question, ctx.owner_id)
        except Exception:  # speculative: retrieve embeds again if this one is missing
            return None

    async def retrieve_raw(query: str, e: asyncio.Task[QueryEmbedding | None]) -> RetrievalResult:
        return await deps.retriever.retrieve(
            deps.sm, ctx.owner_id, state.scope, query, embedding=await e, run_id=ctx.run_id
        )

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
    raw = " ".join(state.question.split())
    r_task: asyncio.Task[RetrievalResult] | None = None
    speculate = (
        deps.gateway.settings.chat_speculative_retrieval
        and bool(state.scope.entries)
        and deps.retriever.config.rerank.kind != "llm"
    )
    if speculate:
        r_task = asyncio.create_task(retrieve_raw(raw, e_task))
        state.spec_retrieval = SpeculativeRetrieval(r_task, raw, state.scope)
    try:
        analysis = await a_task
    except BaseException:
        e_task.cancel()
        await cancel(r_task)
        state.spec_retrieval = None
        raise
    state.embedding = await e_task
    state.analysis = analysis
    state.degraded += analysis.degraded
    state.call_ids += analysis.call_ids
    try:
        state.scope = narrow(state.scope, analysis.target_document_ids)
    except ScopeWideningError:  # never widen; keep the wider scope but leave a trace
        state.reasons.append(TARGET_IGNORED)
    limit_compare(state)  # S18.06: compare needs 2-5 documents in scope, else it is a plain question
    state.route = state.analysis.intent if state.analysis.intent in ROUTE_LIMITS else "qa"
    ctx.budget = TurnBudget(ROUTE_LIMITS[state.route], ctx.budget.deadline_at, ctx.budget.calls_used)
    spec = state.spec_retrieval
    if spec is not None and not _speculation_usable(state, spec):
        # the turn will not retrieve this query: stop the work now (`retrieve` records the miss)
        await cancel(spec.task)
    state.last_decision = {"intent": state.analysis.intent}
    return state


def _speculation_usable(state: TurnState, spec: SpeculativeRetrieval) -> bool:
    """The speculative result is what `retrieve` would compute: same query, same scope, plain retrieval."""
    a = state.analysis
    assert a is not None
    if state.compare or a.clarification is not None or (a.intent == "meta" and a.meta_op is not None):
        return False
    return normalize_query(a.standalone_query) == normalize_query(spec.query) and state.scope == spec.scope
