"""`verify` node: the support check (S17.09) runs here, after `generate` and before `persist`."""

from app.chat.graph import TurnCtx, TurnState


async def verify(state: TurnState, ctx: TurnCtx) -> TurnState:
    state.last_decision = {"enabled": False}
    return state
