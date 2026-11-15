"""Turn events: the typed stream a UI consumes, plus the sink the turn graph emits to.

`TurnEvent` is a pydantic union discriminated on `type`; every event carries `run_id`. The graph emits through
an `EventSink`: `NullSink` for JSON mode (nothing listens), `QueueSink` (unbounded `asyncio.Queue`, so an emit
never blocks the turn, even after the client disconnected) for SSE. Events carry only what is already stored
(segment text, `cited_text`, ids, titles): no prompts, no model names.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
from typing import TYPE_CHECKING, Annotated, Any, Literal, Protocol

from pydantic import BaseModel, Field, TypeAdapter

from app.chat.conflicts import ConflictView

if TYPE_CHECKING:
    from app.chat.citations import ResolvedSegment
    from app.chat.evidence import EvidenceSet

Stage = Literal["analyzing", "retrieving", "reading_documents", "writing", "verifying", "saving"]

# node name -> stage shown to the user; `status` fires when the stage changes (unlisted nodes emit none)
NODE_STAGE: dict[str, Stage] = {
    "load_context": "analyzing",
    "route_question": "analyzing",
    "analyze": "analyzing",
    "retrieve": "retrieving",
    "compare_retrieve": "retrieving",
    "generate": "writing",
    "clarify": "writing",
    "meta": "writing",
    "abstain_no_documents": "writing",
    "abstain_no_evidence": "writing",
    "verify": "verifying",
    "persist": "saving",
}


class _Event(BaseModel):
    run_id: str


class RunStarted(_Event):
    type: Literal["run.started"] = "run.started"
    message_id: str  # the user's message that started the run


class Status(_Event):
    type: Literal["status"] = "status"
    stage: Stage


class EvidenceOut(BaseModel):
    chunk_id: str
    document_id: str | None
    document_title: str | None
    version_no: int | None
    pages: list[int] | None
    sentence_start: int
    sentence_end: int
    cited_text: str


class Segment(_Event):
    type: Literal["segment"] = "segment"
    index: int
    text: str
    support: Literal["cited", "none"]
    evidence: list[EvidenceOut]


class Verdict(BaseModel):
    index: int
    verdict: str


class Verification(_Event):
    type: Literal["verification"] = "verification"
    verdicts: list[Verdict]


class Followups(_Event):
    type: Literal["followups"] = "followups"
    questions: list[str]


class ClarifyOptionOut(BaseModel):
    label: str
    document_id: str


class Clarify(_Event):
    """The turn ended with a question. Text and options come from code, never the model."""

    type: Literal["clarify"] = "clarify"
    question: str
    options: list[ClarifyOptionOut]


class UsageOut(BaseModel):
    input_tokens: int
    output_tokens: int
    cost_usd: float


class Done(_Event):
    type: Literal["done"] = "done"
    message_id: str
    response_type: str
    content: str  # the stored assistant message text (what a reconnecting client shows)
    reason_codes: list[str]
    usage: UsageOut
    latency_ms: int | None
    ttft_ms: int | None
    conflicts: list[ConflictView] = Field(
        default_factory=list,
        description="Sources that disagree, each with the cited evidence of both sides (empty when none).",
    )


class Error(_Event):
    type: Literal["error"] = "error"
    code: str
    retryable: bool


TurnEvent = Annotated[
    RunStarted | Status | Segment | Verification | Followups | Clarify | Done | Error,
    Field(discriminator="type"),
]
_ADAPTER: TypeAdapter[TurnEvent] = TypeAdapter(TurnEvent)


def event_schema() -> dict[str, Any]:
    """JSON Schema of the `TurnEvent` union (served by `GET /v1/chat/events/schema`)."""
    return _ADAPTER.json_schema()


def parse_event(data: str | bytes | dict[str, Any]) -> TurnEvent:
    if isinstance(data, dict):
        return _ADAPTER.validate_python(data)
    return _ADAPTER.validate_json(data)


def to_sse(event: _Event) -> str:
    """One SSE frame: `event:` is the type, `data:` the JSON body (single line)."""
    body = json.dumps(event.model_dump(mode="json"), separators=(",", ":"))
    return f"event: {getattr(event, 'type', '')}\ndata: {body}\n\n"


def segment_event(run_id: object, seg: ResolvedSegment, evidence: EvidenceSet | None) -> Segment:
    """The user-facing form of a resolved segment; evidence fields come from the stored chunks."""
    refs: list[EvidenceOut] = []
    for r in seg.references:
        c = evidence.chunks.get(r.chunk_id) if evidence is not None else None
        refs.append(
            EvidenceOut(
                chunk_id=str(r.chunk_id),
                document_id=str(c.document_id) if c is not None and c.document_id else None,
                document_title=c.doc_title if c is not None else None,
                version_no=c.version_no if c is not None else None,
                pages=[c.page_start, c.page_end] if c is not None and c.page_start else None,
                sentence_start=r.sentence_start,
                sentence_end=r.sentence_end,
                cited_text=r.cited_text,
            )
        )
    support: Literal["cited", "none"] = "cited" if seg.support == "cited" else "none"
    return Segment(run_id=str(run_id), index=seg.ordinal, text=seg.text, support=support, evidence=refs)


class EventSink(Protocol):
    async def emit(self, event: TurnEvent) -> None: ...


class NullSink:
    async def emit(self, event: TurnEvent) -> None:
        return None


class QueueSink:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[TurnEvent] = asyncio.Queue()

    async def emit(self, event: TurnEvent) -> None:
        self.queue.put_nowait(event)


# The SSE handler sets the sink before it starts the turn task (a task copies the context), so the turn runner
# signature stays unchanged and JSON mode never sees a sink.
current_sink: contextvars.ContextVar[EventSink | None] = contextvars.ContextVar("turn_sink", default=None)
