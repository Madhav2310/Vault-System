"""`generate` node: build the evidence set, call the generator, resolve citations (all in `generate`)."""

from app.chat.citations import ResolvedSegment
from app.chat.evidence import build_evidence
from app.chat.graph import TURN_ERROR, TurnCtx, TurnState
from app.chat.nodes._common import ERROR_TEXT, emit_segment, flush_segments, plain
from app.chat.nodes.generate import generate as generate_answer
from app.llm.errors import LlmError


async def generate(state: TurnState, ctx: TurnCtx) -> TurnState:
    assert state.analysis is not None
    state.evidence = build_evidence(state.chunks)

    async def on_segment(seg: ResolvedSegment) -> None:
        await emit_segment(ctx, seg, state.evidence)

    try:
        gen = await generate_answer(
            ctx.deps.gateway, owner_id=ctx.owner_id, evidence=state.evidence,
            question=state.analysis.standalone_query, route="qa", memory=state.memory,
            run_id=ctx.run_id, budget=ctx.budget, on_segment=on_segment,
        )  # fmt: skip
    except LlmError as e:  # a handled failure: the turn completes with an error response
        state.generation = plain("error", ERROR_TEXT, (TURN_ERROR, e.error_type))
        state.last_status, state.last_error = "failed", e.error_type
        return state
    state.generation = gen
    state.last_decision = {"response_type": gen.response_type}
    await flush_segments(state, ctx)
    return state
