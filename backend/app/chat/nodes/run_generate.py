"""`generate` node: build the evidence set, call the generator, resolve citations (all in `generate`)."""

import dataclasses
from typing import Any

from app.chat.citations import ResolvedSegment
from app.chat.compare import compare_call, missing_titles, name_missing_sides
from app.chat.evidence import build_evidence
from app.chat.followups import clean_followups
from app.chat.graph import TURN_ERROR, TurnCtx, TurnState
from app.chat.nodes._common import ERROR_TEXT, abstain_followups, emit_segment, flush_segments, plain
from app.chat.nodes.generate import answer_prompt
from app.chat.nodes.generate import generate as generate_answer
from app.llm.errors import LlmError


def generate_kwargs(state: TurnState, ctx: TurnCtx) -> dict[str, Any]:
    """Every `generate` argument of this turn except `on_segment`."""
    assert state.analysis is not None
    full = state.mode == "full_context"
    call: dict[str, Any] = {"route": state.analysis.intent if full else "qa"}
    call["prompt"] = answer_prompt(ctx.deps.gateway.settings)  # a compare turn overrides it
    call.update(
        compare_call(state, ctx.deps.gateway.settings) if state.compare else {}
    )  # route, prompt, coverage
    return dict(
        gateway=ctx.deps.gateway, owner_id=ctx.owner_id, evidence=state.evidence,
        question=state.analysis.standalone_query, **call,
        mode="full_context" if full else "rag", memory=state.memory,
        run_id=ctx.run_id, budget=ctx.budget,
    )  # fmt: skip


async def generate(state: TurnState, ctx: TurnCtx) -> TurnState:
    assert state.analysis is not None
    state.evidence = build_evidence(state.chunks)

    async def on_segment(seg: ResolvedSegment) -> None:
        ctx.mark_first_segment()
        await emit_segment(ctx, seg, state.evidence)

    call = generate_kwargs(state, ctx)
    try:
        gen = await generate_answer(**call, on_segment=on_segment)
    except LlmError as e:  # a handled failure: the turn completes with an error response
        state.generation = plain("error", ERROR_TEXT, (TURN_ERROR, e.error_type))
        state.last_status, state.last_error = "failed", e.error_type
        return state
    if gen.response_type == "answer":
        questions = [state.question, state.analysis.standalone_query]
        gen = dataclasses.replace(gen, followups=clean_followups(gen.followups, questions))
    elif gen.response_type == "abstain":
        gen = dataclasses.replace(gen, followups=await abstain_followups(state, ctx))
    if state.compare:
        gen = name_missing_sides(gen, missing_titles(state))
    state.generation = gen
    if gen.response_type == "answer" and gen.resolution.segments:
        ctx.mark_first_segment()  # a truncated answer is salvaged after the stream: no earlier segment
    state.last_decision = {"response_type": gen.response_type}
    await flush_segments(state, ctx)
    return state
