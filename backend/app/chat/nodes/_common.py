"""Shared constants and helpers of the turn nodes."""

from decimal import Decimal
from typing import Any

from app.chat.citations import Resolution, ResolvedSegment
from app.chat.nodes.generate import GenerateResult
from app.llm.types import Usage

NO_DOCUMENTS = "SCOPE_EMPTY"
NO_EVIDENCE = "NO_EVIDENCE"
FULL_CONTEXT_UNAVAILABLE = "FULL_CONTEXT_UNAVAILABLE"
TARGET_IGNORED = "TARGET_IGNORED"
NO_DOCUMENTS_TEXT = "No ready documents are in scope. Upload a document or wait for processing to finish."
NO_EVIDENCE_TEXT = "I could not find anything in your documents that answers this."
ERROR_TEXT = "Something went wrong while answering. Please try again."


def plain(response_type: str, text_: str, reasons: tuple[str, ...], res: Any = None) -> GenerateResult:
    """A response written by code (no model call, no citations)."""
    seg = ResolvedSegment(0, text_, "none", ())
    return GenerateResult(
        response_type, Resolution((seg,)), reasons, Usage(), Decimal(0), tuple(getattr(res, "call_ids", ()))
    )
