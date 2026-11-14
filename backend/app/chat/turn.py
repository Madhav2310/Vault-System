"""Turn orchestrator (design §8): the explicit graph in `app.chat.graph` over the nodes in `app.chat.nodes`.

load_context -> route_question -> analyze (embeds the question meanwhile) -> retrieve -> gate -> generate ->
verify -> persist. The routers below, not the nodes, pick the next node. No DB connection is held across
an LLM call or a retrieval call: each phase opens its own short session. Whatever happens, the run is finished
and the session lease released in one `persist_turn` transaction (a failed node becomes an `error` response,
never a stuck run: the graph jumps to `persist`).

`plan_context` (S17.07) picks RAG or the full-context route; the run records `mode=full_context` only when
the whole scoped documents were sent. M0 scope: no evidence floor, no conflict detection (later slices).
"""

import asyncio
import dataclasses
import time
import uuid

from app.chat import sessions as svc
from app.chat.conflicts import conflicts_record
from app.chat.events import Followups, NullSink, current_sink
from app.chat.graph import END, TURN_ERROR, Graph, TurnCtx, TurnDeps, TurnState, router
from app.chat.harness import DEFAULT_LIMITS, TurnBudget
from app.chat.ledger import CandidateRecord, PersistedTurn, TurnRecord, persist_turn
from app.chat.nodes._common import ERROR_TEXT, NO_DOCUMENTS, NO_EVIDENCE, flush_segments, plain
from app.chat.nodes.compare_retrieve import compare_retrieve
from app.chat.nodes.gate import gate
from app.chat.nodes.load_context import load_context
from app.chat.nodes.meta import meta
from app.chat.nodes.plan_context import plan_context
from app.chat.nodes.retrieve import retrieve
from app.chat.nodes.route_question import route_question
from app.chat.nodes.run_analyze import analyze
from app.chat.nodes.run_generate import generate
from app.chat.nodes.terminal import abstain_no_documents, abstain_no_evidence, clarify
from app.chat.nodes.verify import verify
from app.chat.speculative import cancel_all
from app.core.logging import get_logger
from app.llm.gateway import LlmGateway
from app.retrieval.retrieve import RetrievalResult

_log = get_logger("vault.chat.turn")

HEARTBEAT_S = 30.0
__all__ = ["NO_DOCUMENTS", "NO_EVIDENCE", "TURN_ERROR", "TurnDeps", "run_turn"]


def _candidates(r: RetrievalResult) -> tuple[CandidateRecord, ...]:
    return tuple(
        CandidateRecord(
            chunk_id=c.chunk_id,
            vector_rank=c.vector_rank,
            vector_distance=c.vector_distance,
            lexical_rank=c.lexical_rank,
            lexical_score=c.lexical_score,
            id_match=c.id_match,
            rrf_score=c.rrf_score,
            rerank_score=c.rerank_score,
            selected=c.selected,
            expanded_from=c.expanded_from,
            result_index=c.result_index,
        )
        for c in r.candidates
    )


@router("abstain_no_documents", "route_question")
def after_load_context(s: TurnState) -> str:
    return "route_question" if s.scope.entries else "abstain_no_documents"


@router("analyze")
def after_route_question(s: TurnState) -> str:
    return "analyze"  # the fast path skips the call inside the `analyze` node (recorded as a skipped step)


@router("clarify", "meta", "plan_context")
def after_analyze(s: TurnState) -> str:
    a = s.analysis
    if a is not None and a.clarification is not None:
        return "clarify"
    return "meta" if a is not None and a.intent == "meta" and a.meta_op is not None else "plan_context"


@router("retrieve", "compare_retrieve", "generate", "abstain_no_evidence")
def after_plan_context(s: TurnState) -> str:
    if s.mode != "full_context":
        return "compare_retrieve" if s.compare else "retrieve"
    return "generate" if s.chunks else "abstain_no_evidence"  # full context always generates (no gate)


@router("abstain_no_evidence", "gate")
def after_retrieve(s: TurnState) -> str:
    return "gate" if s.chunks else "abstain_no_evidence"


@router("abstain_no_evidence", "generate")
def after_gate(s: TurnState) -> str:
    return "abstain_no_evidence" if s.gate_abstain else "generate"


@router("verify", "persist")
def after_generate(s: TurnState) -> str:
    g = s.generation
    cited = g is not None and any(seg.support == "cited" for seg in g.resolution.segments)
    return "verify" if cited else "persist"


async def persist(state: TurnState, ctx: TurnCtx) -> TurnState:
    """Write the turn's ledger record in one transaction; a failure retries a minimal error record."""
    deps, owner_id = ctx.deps, ctx.owner_id
    await cancel_all(state)  # speculative work nobody used (a failed or short-circuited turn)
    gen = state.generation
    if state.failure or gen is None:
        gen = plain("error", ERROR_TEXT, state.failure or (TURN_ERROR,))
        state.generation = gen
    await flush_segments(state, ctx)  # whatever the stream has not shown yet (the error response, ...)
    retrieval, analysis = state.retrieval, state.analysis
    rec = TurnRecord(
        run_id=ctx.run_id,
        route=state.route,
        mode="full_context" if state.mode == "full_context" else "rag",
        status=state.status,
        scope_version_ids=state.scope.version_ids,
        generation=gen,
        config={
            "prompts": [
                "analyze_query@2",
                "answer_compare@1" if state.compare else "answer_rag@3",
                "support_check@1",
            ],
            "models": _models(deps.gateway),
            "retrieval": deps.retriever.config.version,
        },
        candidates=_candidates(retrieval) if retrieval else (),
        steps=tuple(state.steps),
        call_ids=tuple(dict.fromkeys([*state.call_ids, *gen.call_ids])),
        standalone_query=analysis.standalone_query if analysis else None,
        analysis={"intent": analysis.intent, "dimensions": list(analysis.dimensions)} if analysis else None,
        conflicts=conflicts_record(gen.conflicts),
        reason_codes=(*gen.reasons, *state.reasons),
        degraded=tuple(dict.fromkeys(state.degraded)),
        limits={"max_llm_calls": ctx.budget.limits.max_llm_calls, "deadline_s": ctx.budget.limits.deadline_s},
        latency_ms=int((time.monotonic() - ctx.t_start) * 1000),
        ttft_ms=None if state.failure else ctx.ttft_ms,
    )
    try:
        async with deps.sm() as s:
            state.persisted = await persist_turn(s, owner_id, rec)
        if gen.followups:  # what was stored, sent before `done`
            await ctx.emit(Followups(run_id=str(ctx.run_id), questions=list(gen.followups)))
        return state
    except Exception as e:
        # A ledger/DB failure must not leave the run `running` and the session leased: retry minimally.
        _log.error("persist_failed", error_type=type(e).__name__)
        minimal = dataclasses.replace(
            rec,
            status="failed",
            generation=plain("error", ERROR_TEXT, (TURN_ERROR, "PERSIST_FAILED")),
            candidates=(),
            steps=(),
            call_ids=(),
            reason_codes=(TURN_ERROR, "PERSIST_FAILED"),
        )
        try:
            async with deps.sm() as s:
                state.persisted = await persist_turn(s, owner_id, minimal)
                return state
        except Exception as e2:
            _log.error("persist_fallback_failed", error_type=type(e2).__name__)
            async with deps.sm() as s:
                await svc.release_turn(s, ctx.session_id, owner_id, ctx.run_id)
            raise


def build_graph() -> Graph:
    return Graph(
        nodes={
            "load_context": load_context, "route_question": route_question, "analyze": analyze,
            "plan_context": plan_context, "retrieve": retrieve, "compare_retrieve": compare_retrieve,
            "gate": gate, "generate": generate,
            "verify": verify,
            "clarify": clarify, "meta": meta, "abstain_no_documents": abstain_no_documents,
            "abstain_no_evidence": abstain_no_evidence, "persist": persist,
        },
        edges={
            "load_context": after_load_context, "route_question": after_route_question,
            "analyze": after_analyze, "plan_context": after_plan_context, "retrieve": after_retrieve,
            "compare_retrieve": after_retrieve,
            "gate": after_gate,
            "generate": after_generate, "verify": "persist", "clarify": "persist", "meta": "persist",
            "abstain_no_documents": "persist", "abstain_no_evidence": "persist", "persist": END,
        },
        start="load_context",
        on_error="persist",
    )  # fmt: skip


GRAPH = build_graph()


async def _run_turn(
    deps: TurnDeps,
    owner_id: uuid.UUID,
    session_id: uuid.UUID,
    run_id: uuid.UUID,
    user_message_id: uuid.UUID,
    question: str,
    mode: str = "auto",
) -> PersistedTurn:
    """Run one turn to completion and persist it. Idempotent per run (a finished run is returned as is)."""
    ctx = TurnCtx(
        deps, owner_id, session_id, run_id, user_message_id, TurnBudget(DEFAULT_LIMITS),
        sink=current_sink.get() or NullSink(),
    )  # fmt: skip
    state = await GRAPH.run(TurnState(question=question, mode=mode), ctx)
    assert state.persisted is not None
    result: PersistedTurn = state.persisted
    return result


async def run_turn(
    deps: TurnDeps,
    owner_id: uuid.UUID,
    session_id: uuid.UUID,
    run_id: uuid.UUID,
    user_message_id: uuid.UUID,
    question: str,
    mode: str = "auto",
) -> PersistedTurn:
    """`_run_turn` with a lease heartbeat, so a slow turn keeps its session while it runs."""

    async def beat() -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_S)
            async with deps.sm() as s:
                if not await svc.extend_lease(s, session_id, owner_id, run_id):
                    return

    hb = asyncio.ensure_future(beat())
    try:
        return await _run_turn(deps, owner_id, session_id, run_id, user_message_id, question, mode)
    finally:
        hb.cancel()


def _models(gw: LlmGateway) -> dict[str, str]:
    s = gw.settings
    return {
        "fast": s.model_fast,
        "balanced": s.model_balanced,
        "deep": s.model_deep,
        "flagship": s.model_flagship,
    }
