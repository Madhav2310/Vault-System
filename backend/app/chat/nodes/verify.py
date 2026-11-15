"""`verify` node: the support check runs here, after `generate` and before `persist`.

It is advisory: it rewrites the resolved segments with their verdicts and adds reason codes, and never fails
the turn (`check_support` does not raise for model, provider, timeout or budget problems).
"""

import dataclasses

from app.chat.citations import Resolution
from app.chat.events import Verdict, Verification
from app.chat.graph import TurnCtx, TurnState
from app.chat.nodes.generate import GenerateResult
from app.chat.support_check import UNSUPPORTED, UNSUPPORTED_CLAIM, SupportResult, check_support


def _with_verdicts(gen: GenerateResult, res: SupportResult) -> GenerateResult:
    checked = tuple(
        dataclasses.replace(s, verification=v.verdict, verification_reason=v.reason)
        for s in gen.resolution.segments
        for v in (res.verdicts[s.ordinal],)
    )
    return dataclasses.replace(
        gen, resolution=Resolution(checked, gen.resolution.reasons, gen.resolution.unresolved_count)
    )


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
    await ctx.emit(Verification(run_id=str(ctx.run_id), verdicts=events))
    return state
