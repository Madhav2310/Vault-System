"""Code-written responses: the abstentions (no model call)."""

import dataclasses

from app.chat.graph import TurnCtx, TurnState
from app.chat.nodes._common import (
    NO_DOCUMENTS,
    NO_DOCUMENTS_TEXT,
    NO_EVIDENCE,
    NO_EVIDENCE_TEXT,
    abstain_followups,
    flush_segments,
    plain,
)


async def abstain_no_documents(state: TurnState, ctx: TurnCtx) -> TurnState:
    state.route = "meta"  # recorded as meta: no retrieval or model call
    state.generation = plain("abstain", NO_DOCUMENTS_TEXT, (NO_DOCUMENTS,))
    await flush_segments(state, ctx)
    return state


async def abstain_no_evidence(state: TurnState, ctx: TurnCtx) -> TurnState:
    base = plain("abstain", NO_EVIDENCE_TEXT, (NO_EVIDENCE,))
    state.generation = dataclasses.replace(base, followups=await abstain_followups(state, ctx))
    await flush_segments(state, ctx)
    return state
