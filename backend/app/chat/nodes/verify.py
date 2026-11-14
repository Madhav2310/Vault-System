"""`verify` node (S17.09, S22.06): the support check runs here, after `generate` and before `persist`.

With `chat_support_version` 1 (default) it is advisory only: it rewrites the resolved segments with their
verdicts and adds reason codes, and never fails the turn (`check_support` does not raise for model, provider,
timeout or budget problems).

With 2 (`support_check@2`) the same single call also repairs the answer in code: unneeded citations are
pruned, an unsupported segment is removed (`UNSUPPORTED_REMOVED`; a contradicted one is kept and flagged),
and when every segment fails the answer is written once more (`ANSWER_REGENERATED`, at most once, inside the
turn budget). If that fails too the answer is the warm abstain text (`UNSUPPORTED_ABSTAINED`). The stream
learns of it through the `verification` event (verdict `removed` for dropped segments, new segments for a
regenerated answer) and `done.content`; what is persisted is what the user finally sees.
"""

import dataclasses

from app.chat.citations import Resolution, ResolvedSegment
from app.chat.events import Verdict, Verification
from app.chat.followups import clean_followups
from app.chat.graph import TurnCtx, TurnState
from app.chat.nodes._common import abstain_followups, emit_segment, plain
from app.chat.nodes.generate import GenerateResult
from app.chat.nodes.generate import generate as generate_answer
from app.chat.nodes.run_generate import generate_kwargs
from app.chat.support_check import (
    ANSWER_REGENERATED,
    UNSUPPORTED,
    UNSUPPORTED_ABSTAINED,
    UNSUPPORTED_CLAIM,
    UNSUPPORTED_REMOVED,
    SupportResult,
    all_failed,
    check_support,
    repair,
)
from app.chat.support_check import (
    Verdict as CheckVerdict,
)
from app.explain.templates import UNSUPPORTED_ABSTAIN_TEXT
from app.llm.errors import LlmError

REMOVED = "removed"  # `verification` event verdict of a segment dropped from the answer


def _with_verdicts(gen: GenerateResult, res: SupportResult) -> GenerateResult:
    checked = tuple(
        dataclasses.replace(s, verification=v.verdict, verification_reason=v.reason)
        for s in gen.resolution.segments
        for v in (res.verdicts[s.ordinal],)
    )
    return dataclasses.replace(
        gen, resolution=Resolution(checked, gen.resolution.reasons, gen.resolution.unresolved_count)
    )


async def _regenerate(
    state: TurnState, ctx: TurnCtx, offset: int
) -> tuple[GenerateResult, SupportResult] | None:
    """One more answer (no streaming), checked once. None when it cannot be had or fails the check again.
    An answer whose check could not run is kept unchecked (the check is advisory everywhere, design 8.5).
    Its segments are numbered after the ones already streamed, so the stream's indexes stay unique."""
    assert state.analysis is not None
    if state.compare:  # compare answers carry side-naming rules that live in `generate`: no repeat
        return None
    state.reasons.append(ANSWER_REGENERATED)
    try:
        gen = await generate_answer(**generate_kwargs(state, ctx))
    except LlmError:
        return None
    state.call_ids += gen.call_ids
    if gen.response_type != "answer" or not gen.resolution.segments:
        return None
    questions = [state.question, state.analysis.standalone_query]
    segs = tuple(dataclasses.replace(s, ordinal=s.ordinal + offset) for s in gen.resolution.segments)
    gen = dataclasses.replace(
        gen,
        resolution=Resolution(segs, gen.resolution.reasons, gen.resolution.unresolved_count),
        followups=clean_followups(gen.followups, questions),
    )
    res = await check_support(
        ctx.deps.gateway, segs, owner_id=ctx.owner_id, run_id=ctx.run_id, budget=ctx.budget,
        evidence=state.evidence,
    )  # fmt: skip
    state.call_ids += res.call_ids
    state.degraded += res.degraded
    if (
        res.reasons
    ):  # the check itself could not run (e.g. the turn's call budget is spent): advisory, as in v1
        state.reasons += res.reasons
        return gen, res
    if all_failed(segs, res.verdicts):
        return None
    return gen, res


async def _repair(state: TurnState, ctx: TurnCtx, gen: GenerateResult, res: SupportResult) -> list[Verdict]:
    """Apply `support_check@2` to `state.generation` (already carrying the verdicts); returns the
    `verification` event verdicts, one per original segment plus those of a regenerated answer."""
    segments = gen.resolution.segments
    events: dict[int, str] = {s.ordinal: str(res.verdicts[s.ordinal].verdict) for s in segments}
    verdicts = dict(res.verdicts)
    if all_failed(segments, verdicts):
        state.call_ids += gen.call_ids  # the draft's call stays on the run even if it is replaced
        again = await _regenerate(state, ctx, max(s.ordinal for s in segments) + 1)
        if again is not None:
            new_gen, new_res = again
            events.update({o: REMOVED for o in events})
            new_gen = _with_verdicts(new_gen, new_res)
            state.generation = gen = new_gen
            segments, verdicts, res = new_gen.resolution.segments, dict(new_res.verdicts), new_res
            events.update({s.ordinal: str(verdicts[s.ordinal].verdict) for s in segments})
            for s in segments:  # shown after the verdicts of the draft they replace
                await emit_segment(ctx, s, state.evidence)
        else:
            base = plain("abstain", UNSUPPORTED_ABSTAIN_TEXT, (UNSUPPORTED_ABSTAINED,))
            state.generation = dataclasses.replace(base, followups=await abstain_followups(state, ctx))
            state.reasons.append(UNSUPPORTED_REMOVED)
            state.last_decision = {"enabled": True, "repair": "abstained"}
            return [Verdict(index=o, verdict=REMOVED) for o in sorted(events)]
    fixed = repair(segments, verdicts, state.evidence)
    removed = list(fixed.removed)
    if removed:
        state.reasons.append(UNSUPPORTED_REMOVED)
        events.update({o: REMOVED for o in removed})
    if fixed.removed or fixed.pruned:
        state.generation = dataclasses.replace(
            gen,
            resolution=Resolution(fixed.segments, gen.resolution.reasons, gen.resolution.unresolved_count),
        )
    state.last_decision = {
        "enabled": True,
        "verdicts": _counts(verdicts, fixed.segments),
        "removed": len(removed),
        "pruned": fixed.pruned,
    }
    return [Verdict(index=o, verdict=v) for o, v in sorted(events.items())]


def _counts(verdicts: dict[int, CheckVerdict], kept: tuple[ResolvedSegment, ...]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for s in kept:
        v = verdicts[s.ordinal].verdict
        counts[v] = counts.get(v, 0) + 1
    return counts


async def verify(state: TurnState, ctx: TurnCtx) -> TurnState:
    gen = state.generation
    if gen is None or gen.response_type != "answer":
        state.last_decision = {"enabled": False}
        await ctx.emit(Verification(run_id=str(ctx.run_id), verdicts=[]))
        return state
    segments = gen.resolution.segments
    res = await check_support(
        ctx.deps.gateway, segments, owner_id=ctx.owner_id, run_id=ctx.run_id, budget=ctx.budget,
        evidence=state.evidence,
    )  # fmt: skip
    if not res.called and not res.reasons:  # nothing cited: nothing was asked, nothing to record
        state.last_decision = {"enabled": False}
        await ctx.emit(Verification(run_id=str(ctx.run_id), verdicts=[]))
        return state
    gen = _with_verdicts(gen, res)
    state.generation = gen
    state.call_ids += res.call_ids
    state.degraded += res.degraded
    state.reasons += res.reasons
    repairing = ctx.deps.gateway.settings.chat_support_version == 2 and res.called and not res.reasons
    if repairing:
        verdicts = await _repair(state, ctx, gen, res)
        final = state.generation
        if final is not None and any(
            s.verification == UNSUPPORTED for s in final.resolution.segments
        ):  # a contradicted segment that was kept
            state.reasons.append(UNSUPPORTED_CLAIM)
        await ctx.emit(Verification(run_id=str(ctx.run_id), verdicts=verdicts))
        return state
    counts: dict[str, int] = {}
    for v in res.verdicts.values():
        counts[v.verdict] = counts.get(v.verdict, 0) + 1
    if counts.get(UNSUPPORTED):
        state.reasons.append(UNSUPPORTED_CLAIM)
    state.last_decision = {"enabled": True, "verdicts": counts}
    events = [Verdict(index=int(i), verdict=str(v.verdict)) for i, v in sorted(res.verdicts.items())]
    await ctx.emit(Verification(run_id=str(ctx.run_id), verdicts=events))
    return state
