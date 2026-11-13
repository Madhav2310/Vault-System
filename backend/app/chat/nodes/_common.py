"""Shared constants and helpers of the turn nodes."""

from decimal import Decimal
from typing import Any

from app.chat.citations import Resolution, ResolvedSegment
from app.chat.events import segment_event
from app.chat.followups import brief_questions
from app.chat.nodes.generate import GenerateResult
from app.llm.types import Usage

NO_DOCUMENTS = "SCOPE_EMPTY"
NO_EVIDENCE = "NO_EVIDENCE"
FULL_CONTEXT_UNAVAILABLE = "FULL_CONTEXT_UNAVAILABLE"
TARGET_IGNORED = "TARGET_IGNORED"
NO_DOCUMENTS_TEXT = "No ready documents are in scope. Upload a document or wait for processing to finish."
NO_EVIDENCE_TEXT = "I could not find anything in your documents that answers this."
ERROR_TEXT = "Something went wrong while answering. Please try again."


async def abstain_followups(state: Any, ctx: Any) -> tuple[str, ...]:
    """Up to three suggested questions from the briefs of the documents in scope (S18.04)."""
    async with ctx.deps.sm() as db:
        return await brief_questions(db, ctx.owner_id, state.scope.version_ids, [state.question])


async def emit_segment(ctx: Any, seg: ResolvedSegment, evidence: Any) -> None:
    """Send one `segment` event and count it (the count is the next index)."""
    ctx.segments_emitted += 1
    await ctx.emit(segment_event(ctx.run_id, seg, evidence))


async def flush_segments(state: Any, ctx: Any) -> None:
    """Send the segments of the final generation that were not streamed yet (code-written responses,
    a truncated answer, the error response), so the stream always ends up with every persisted segment."""
    gen = state.generation
    if gen is None:
        return
    for seg in gen.resolution.segments[ctx.segments_emitted :]:
        await emit_segment(ctx, seg, state.evidence)


def plain(response_type: str, text_: str, reasons: tuple[str, ...], res: Any = None) -> GenerateResult:
    """A response written by code (no model call, no citations)."""
    seg = ResolvedSegment(0, text_, "none", ())
    return GenerateResult(
        response_type, Resolution((seg,)), reasons, Usage(), Decimal(0), tuple(getattr(res, "call_ids", ()))
    )
