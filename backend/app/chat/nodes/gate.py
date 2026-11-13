"""`gate` node: the evidence gate (S17.08 fills in the floor; until then evidence that exists passes)."""

from app.chat.graph import TurnCtx, TurnState


async def gate(state: TurnState, ctx: TurnCtx) -> TurnState:
    state.last_decision = {"passed": True}
    return state
