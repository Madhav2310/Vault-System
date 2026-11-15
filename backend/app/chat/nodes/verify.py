"""`verify` node: the support check runs here, after `generate` and before `persist`.

It is advisory: it rewrites the resolved segments with their verdicts and adds reason codes, and never fails
the turn (`check_support` does not raise for model, provider, timeout or budget problems).
"""

import dataclasses

from app.chat.abstain import (
    ABSTAIN_SCORED,
    AbstainConfigError,
    apply_partial,
    decide,
    load_config,
    retrieval_features,
    unsupported_share,
    with_generation_features,
)
from app.chat.citations import Resolution
from app.chat.events import Verdict, Verification
from app.chat.gate import abstain_text
from app.chat.graph import TurnCtx, TurnState
from app.chat.nodes._common import abstain_followups, emit_segment, plain
from app.chat.nodes.generate import GenerateResult
from app.chat.support_check import UNSUPPORTED, UNSUPPORTED_CLAIM, SupportResult, check_support
from app.retrieval.rerank import DEGRADED_NO_RERANK

REMOVED = "removed"  # `verification` event verdict of a streamed segment withdrawn by an abstain


def _with_verdicts(gen: GenerateResult, res: SupportResult) -> GenerateResult:
    checked = tuple(
        dataclasses.replace(s, verification=v.verdict, verification_reason=v.reason)
        for s in gen.resolution.segments
        for v in (res.verdicts[s.ordinal],)
    )
    return dataclasses.replace(
        gen, resolution=Resolution(checked, gen.resolution.reasons, gen.resolution.unresolved_count)
    )


async def _scored_final(
    state: TurnState, ctx: TurnCtx, gen: GenerateResult, res: SupportResult, events: list[Verdict]
) -> list[Verdict]:
    """Post-generation decision (flag `chat_abstain_scored`): record the combiner inputs on the run
    and, with a fitted `final` model and every feature present, keep the answer, add the partial-answer
    segment or replace the answer with the warm abstain text. `gen` is the answer as checked. No model
    call; anything missing keeps the old behaviour. Returns the `verification` verdicts."""
    share = unsupported_share(str(v.verdict) for v in res.verdicts.values())
    state.abstain_features = {"unsupported": share}
    try:
        cfg = load_config()
    except AbstainConfigError:
        return events
    r, cur = state.retrieval, state.generation
    if (
        r is None
        or cur is None
        or cur.response_type != "answer"
        or state.compare
        or state.mode == "full_context"
        or DEGRADED_NO_RERANK in r.degraded
        or any(c.id_match for c in r.candidates)
    ):
        return events
    x = with_generation_features(retrieval_features(r.candidates, cfg.margin_rank), share)
    d = decide(cfg, x, stage="final")
    if d.model != "final":
        return events
    state.abstain_features.update({"outcome": d.outcome, "p": round(d.p or 0.0, 4)})
    if d.outcome == "answer":
        return events
    state.last_decision = {**(state.last_decision or {}), "abstain_scored": d.outcome}
    if d.outcome == "partial":
        resolution, codes = apply_partial(cur.resolution)
        segs = list(resolution.segments)
        last = segs[-1]
        segs[-1] = dataclasses.replace(
            last,
            ordinal=max(
                ctx.segments_emitted, max((s.ordinal for s in cur.resolution.segments), default=-1) + 1
            ),
        )  # unique among the indexes already streamed
        state.generation = dataclasses.replace(
            cur, resolution=dataclasses.replace(resolution, segments=tuple(segs))
        )
        state.reasons += codes
        if ctx.segments_emitted >= len(gen.resolution.segments):  # everything else was streamed: send it too
            await emit_segment(ctx, segs[-1], state.evidence)
        return events
    state.call_ids += cur.call_ids
    base = plain("abstain", abstain_text(len(state.scope.entries)), (ABSTAIN_SCORED,))
    state.generation = dataclasses.replace(base, followups=await abstain_followups(state, ctx))
    return [Verdict(index=v.index, verdict=REMOVED) for v in events]


async def verify(state: TurnState, ctx: TurnCtx) -> TurnState:
    gen = state.generation
    if gen is None or gen.response_type != "answer":
        state.last_decision = {"enabled": False}
        await ctx.emit(Verification(run_id=str(ctx.run_id), verdicts=[]))
        return state
    segments = gen.resolution.segments
    res = await check_support(
        ctx.deps.gateway, segments, owner_id=ctx.owner_id, run_id=ctx.run_id, budget=ctx.budget
    )
    if not res.called and not res.reasons:  # nothing cited: nothing was asked, nothing to record
        state.last_decision = {"enabled": False}
        await ctx.emit(Verification(run_id=str(ctx.run_id), verdicts=[]))
        return state
    gen = _with_verdicts(gen, res)
    state.generation = gen
    state.call_ids += res.call_ids
    state.degraded += res.degraded
    state.reasons += res.reasons
    counts: dict[str, int] = {}
    for v in res.verdicts.values():
        counts[v.verdict] = counts.get(v.verdict, 0) + 1
    if counts.get(UNSUPPORTED):
        state.reasons.append(UNSUPPORTED_CLAIM)
    state.last_decision = {"enabled": True, "verdicts": counts}
    events = [Verdict(index=int(i), verdict=str(v.verdict)) for i, v in sorted(res.verdicts.items())]
    if ctx.deps.gateway.settings.chat_abstain_scored:
        events = await _scored_final(state, ctx, gen, res, events)
    await ctx.emit(Verification(run_id=str(ctx.run_id), verdicts=events))
    return state
