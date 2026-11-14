"""`generate` node: build the evidence set, call the generator, resolve citations (all in `generate`)."""

import dataclasses
import uuid
from typing import Any

from app.chat.calc import calc_call
from app.chat.citations import ResolvedSegment
from app.chat.compare import Repair, compare_call, coverage_gate, missing_titles, name_missing_sides, quota_on
from app.chat.coverage import plan_call
from app.chat.evidence import build_evidence
from app.chat.followups import clean_followups
from app.chat.graph import TURN_ERROR, TurnCtx, TurnState
from app.chat.nodes._common import ERROR_TEXT, abstain_followups, emit_segment, flush_segments, plain
from app.chat.nodes.generate import GenerateResult
from app.chat.nodes.generate import generate as generate_answer
from app.chat.voice import generate_call as voice_call
from app.llm.errors import LlmError


def generate_kwargs(state: TurnState, ctx: TurnCtx) -> dict[str, Any]:
    """Every `generate` argument of this turn except `on_segment` (shared with the S22.06 regeneration)."""
    assert state.analysis is not None
    full = state.mode == "full_context"
    call: dict[str, Any] = {"route": state.analysis.intent if full else "qa"}
    call.update(
        compare_call(state, ctx.deps.gateway.settings) if state.compare else {}
    )  # S18.06: route, prompt, coverage (S22.05: quota prompt when switched on)
    call.update(plan_call(state, ctx.deps.gateway.settings, full))  # S22.04: empty unless switched on
    call.update(calc_call(state, ctx.deps.gateway.settings, full))  # S22.08: empty unless switched on
    call.update(voice_call(ctx.deps.gateway.settings))  # S23.01: empty unless switched on
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
    if state.compare and quota_on(ctx.deps.gateway.settings):
        gen = await coverage_gate(state, gen, _repair(state, ctx, call))  # S22.05
    elif state.compare:
        gen = name_missing_sides(gen, missing_titles(state))
    state.generation = gen
    if gen.response_type == "answer" and gen.resolution.segments:
        ctx.mark_first_segment()  # a truncated answer is salvaged after the stream: no earlier segment
    state.last_decision = {"response_type": gen.response_type}
    await flush_segments(state, ctx)
    return state


def _repair(state: TurnState, ctx: TurnCtx, call: dict[str, Any]) -> Repair:
    """The one targeted repair call of a compare side that has evidence but no citation (S22.05): a
    generation over that document's chunks only, inside the turn budget (an exhausted budget raises)."""
    assert state.analysis is not None

    async def repair(doc_id: uuid.UUID, title: str) -> GenerateResult | None:
        own = [c for c in state.chunks if c.document_id == doc_id]
        return await generate_answer(
            ctx.deps.gateway, owner_id=ctx.owner_id, evidence=build_evidence(own),
            question=f"{state.analysis.standalone_query} (answer only for the document '{title}')",  # type: ignore[union-attr]
            route="compare", prompt=call["prompt"], memory=None,
            coverage=f"Only the document '{title}' is in scope for this answer.",
            run_id=ctx.run_id, budget=ctx.budget, system_suffix=call.get("system_suffix"),
        )  # fmt: skip

    return repair
