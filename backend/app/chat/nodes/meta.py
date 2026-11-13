"""`meta` node (S18.05): run the analysis' allowlisted op and write the answer from the rows, in code.

No model call and no retrieval. The session lease and DB connection are held only for the query itself.
"""

from decimal import Decimal

from app.chat.citations import Resolution
from app.chat.graph import TurnCtx, TurnState
from app.chat.meta import run_op
from app.chat.nodes._common import flush_segments
from app.chat.nodes.generate import GenerateResult
from app.llm.types import Usage


async def meta(state: TurnState, ctx: TurnCtx) -> TurnState:
    assert state.analysis is not None and state.analysis.meta_op is not None
    op = state.analysis.meta_op
    async with ctx.deps.sm() as db:
        answer = await run_op(db, ctx.owner_id, op, state.scope.version_ids)
        await db.rollback()  # read only
    state.generation = GenerateResult(
        "answer", Resolution(answer.segments), (answer.reason,), Usage(), Decimal(0), ()
    )
    state.last_decision = {"op": op.op}
    await flush_segments(state, ctx)
    return state
