"""`route_question` node: the S17.10 fast-path rules will live here; until then everything is analyzed."""

from app.chat.graph import TurnCtx, TurnState


async def route_question(state: TurnState, ctx: TurnCtx) -> TurnState:
    state.last_decision = {"next": "analyze"}
    return state
