"""`plan_context` node (design §8.2): decide RAG vs full context, and for full context load the evidence.

Runs after `analyze`. RAG leaves the state untouched (the graph goes on to `retrieve`). Full context loads
every chunk of the scoped versions in order into `state.chunks`, records each as a selected candidate (vector
and lexical fields null), and sets `state.mode = "full_context"`. If the assembled prompt would exceed
`full_context_max_tokens` + 4,000 tokens, it falls back to RAG (reason `FULL_CONTEXT_TOO_LARGE` when full
context was requested). Counting tokens is CPU work and runs in a worker thread.
"""

import asyncio

from app.chat.evidence import build_evidence
from app.chat.full_context import (
    FULL_CONTEXT_TOO_LARGE,
    PROMPT_MARGIN_TOKENS,
    load_scope_chunks,
    plan_mode,
    version_tokens,
)
from app.chat.graph import TurnCtx, TurnState
from app.chat.nodes.generate import compose_user_message
from app.ingestion.chunk import count_tokens
from app.retrieval.retrieve import Candidate, RetrievalResult


async def plan_context(state: TurnState, ctx: TurnCtx) -> TurnState:
    assert state.analysis is not None
    deps, settings = ctx.deps, ctx.deps.gateway.settings
    requested = state.mode
    async with deps.sm() as s:
        tokens = await version_tokens(s, ctx.owner_id, state.scope)
    plan = plan_mode(state.scope, state.analysis, requested, tokens, settings)
    decision: dict[str, object] = {
        "requested": requested,
        "mode": plan.mode,
        "scope_tokens": sum(t or 0 for t in tokens),
    }
    state.last_decision = decision
    state.mode = plan.mode
    if plan.reason:
        state.reasons.append(plan.reason)
        decision["reason"] = plan.reason
    if plan.mode != "full_context":
        return state

    async with deps.sm() as s:
        chunks = await load_scope_chunks(s, ctx.owner_id, state.scope)
    message = compose_user_message(
        build_evidence(chunks), state.analysis.standalone_query, state.analysis.intent, state.memory
    )
    prompt_tokens = await asyncio.to_thread(count_tokens, message)
    decision["prompt_tokens"] = prompt_tokens
    if prompt_tokens > settings.full_context_max_tokens + PROMPT_MARGIN_TOKENS:
        state.mode = "rag"
        decision.update(mode="rag", reason=FULL_CONTEXT_TOO_LARGE)
        if requested == "full_context":
            state.reasons.append(FULL_CONTEXT_TOO_LARGE)
        return state

    state.chunks = chunks
    state.retrieval = RetrievalResult(
        candidates=tuple(
            Candidate(c.chunk_id, c.version_id, selected=True, result_index=i) for i, c in enumerate(chunks)
        ),
        selected=tuple(c.chunk_id for c in chunks),
        degraded=(),
        config_version=deps.retriever.config.version,
    )
    decision["chunks"] = len(chunks)
    return state
