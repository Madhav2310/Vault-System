"""Code-written responses: abstentions and the clarification question (no model call)."""

from app.chat.graph import TurnCtx, TurnState
from app.chat.nodes._common import (
    NO_DOCUMENTS,
    NO_DOCUMENTS_TEXT,
    NO_EVIDENCE,
    NO_EVIDENCE_TEXT,
    flush_segments,
    plain,
)


async def abstain_no_documents(state: TurnState, ctx: TurnCtx) -> TurnState:
    state.route = "meta"
    state.generation = plain("abstain", NO_DOCUMENTS_TEXT, (NO_DOCUMENTS,))
    await flush_segments(state, ctx)
    return state


async def abstain_no_evidence(state: TurnState, ctx: TurnCtx) -> TurnState:
    state.generation = plain("abstain", NO_EVIDENCE_TEXT, (NO_EVIDENCE,))
    await flush_segments(state, ctx)
    return state


async def clarify(state: TurnState, ctx: TurnCtx) -> TurnState:
    assert state.analysis is not None and state.analysis.clarification is not None
    c = state.analysis.clarification
    body = c.question + (" " + " / ".join(c.options) if c.options else "")
    state.generation = plain("clarify", body, state.analysis.reason_codes)
    await flush_segments(state, ctx)
    return state
