"""`verify` node: the support check (S17.09) runs here, after `generate` and before `persist`."""

from app.chat.events import Verdict, Verification
from app.chat.graph import TurnCtx, TurnState


async def verify(state: TurnState, ctx: TurnCtx) -> TurnState:
    state.last_decision = {"enabled": False}
    # S17.09 fills `state.verdicts` (segment index -> verdict); until then the event carries no verdicts.
    verdicts = [Verdict(index=int(i), verdict=str(v)) for i, v in sorted(state.verdicts.items())]
    await ctx.emit(Verification(run_id=str(ctx.run_id), verdicts=verdicts))
    return state
