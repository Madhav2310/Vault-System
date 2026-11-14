"""Code-written responses: abstentions and the clarification question (no model call)."""

import dataclasses

from app.chat.abstain import ABSTAIN_SCORED
from app.chat.events import Clarify, ClarifyOptionOut
from app.chat.gate import ABSTAIN_NO_EVIDENCE, abstain_text
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
    state.route = "meta"
    state.generation = plain("abstain", NO_DOCUMENTS_TEXT, (NO_DOCUMENTS,))
    await flush_segments(state, ctx)
    return state


def _scored_gate(state: TurnState, ctx: TurnCtx) -> bool:
    """The gate abstained through the fitted combiner (its step names the model), not the floor."""
    return ctx.deps.gateway.settings.chat_abstain_scored and any(
        s.node == "gate" and s.decision is not None and "scored" in s.decision for s in state.steps
    )


async def abstain_no_evidence(state: TurnState, ctx: TurnCtx) -> TurnState:
    if state.gate_abstain:  # below the evidence floor: a template naming the documents in scope
        code = ABSTAIN_SCORED if _scored_gate(state, ctx) else ABSTAIN_NO_EVIDENCE
        base = plain("abstain", abstain_text(len(state.scope.entries)), (code,))
    else:
        base = plain("abstain", NO_EVIDENCE_TEXT, (NO_EVIDENCE,))
    state.generation = dataclasses.replace(base, followups=await abstain_followups(state, ctx))
    await flush_segments(state, ctx)
    return state


async def clarify(state: TurnState, ctx: TurnCtx) -> TurnState:
    """The turn ends with a question: template text, options from real document titles."""
    c = state.clarification
    assert c is not None
    state.route = "clarify"
    state.generation = plain("clarify", c.text, (c.reason,))
    await flush_segments(state, ctx)
    await ctx.emit(
        Clarify(
            run_id=str(ctx.run_id),
            question=c.text,
            options=[ClarifyOptionOut(label=o.label, document_id=str(o.document_id)) for o in c.options],
        )
    )
    state.last_decision = {"options": len(c.options), "reason": c.reason}
    return state
