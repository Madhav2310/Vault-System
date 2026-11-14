"""`route_question` node: the fast-path rules decide whether `analyze` makes its model call."""

from app.chat.graph import TurnCtx, TurnState
from app.chat.routing_rules import fast_path


async def route_question(state: TurnState, ctx: TurnCtx) -> TurnState:
    prior = len(state.memory.turns) if state.memory is not None else 0
    d = fast_path(state.question, prior_turns=prior, scope_docs=len(state.scope.entries))
    state.fast_path = d.skip_analyze and ctx.deps.gateway.settings.chat_fast_path
    state.last_decision = {"next": "analyze", "fast_path": state.fast_path, "rule": d.why}
    return state
