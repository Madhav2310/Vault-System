"""Response models of the public API (design §12), used for OpenAPI and response validation (S18.11).

Handlers build plain dicts; these models give each body a name, typed top-level fields and an example. Nested,
open-ended structures (facts, segments, glyphs, per-stage metrics) stay `dict[str, Any]`. Models allow extra
keys so adding a field to a handler never turns into a 500; routes that return optional fields use
`response_model_exclude_unset=True` so a key the handler did not set is not invented as `null`.
"""

import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

DOC_ID = "0192f3a4-7b1c-7c3e-9a55-3d2f6c1e8a10"
VERSION_ID = "0192f3a4-7b1d-7a02-8e11-5b7c9d0e4f21"
RUN_ID = "0192f3a5-0c11-7d44-b3a2-6e8f1a2b3c4d"
SESSION_ID = "0192f3a4-e001-7f10-a2c4-1d3e5f708192"
MESSAGE_ID = "0192f3a5-0c12-7e55-8c66-9a0b1c2d3e4f"
CHUNK_ID = "0192f3a4-9a01-7b33-9d44-0e1f2a3b4c5d"
TS = "2026-11-13T14:51:29.224375+00:00"


class Model(BaseModel):
    model_config = ConfigDict(extra="allow")


class ErrorResponse(BaseModel):
    """`application/problem+json` body of every 4xx/5xx (RFC 9457 shape plus `code` and `retryable`)."""

    type: str = Field(description="Problem type URI", examples=["https://vault.local/problems/not_found"])
    title: str = Field(examples=["Not found"])
    status: int = Field(examples=[404])
    code: str = Field(description="Stable machine-readable error code", examples=["not_found"])
    detail: str | None = Field(default=None, examples=["Original file is not available"])
    retryable: bool = Field(description="Whether retrying the same request can succeed", examples=[False])
    errors: list[dict[str, Any]] | None = Field(
        default=None, description="Validation errors (422): field location, message and type only"
    )


# --- documents ---------------------------------------------------------------------------------------------
class UploadLinks(Model):
    document: str
    processing: str


class UploadAccepted(Model):
    document_id: uuid.UUID
    version_id: uuid.UUID
    job_id: uuid.UUID | None
    status: str
    deduplicated: bool
    links: UploadLinks
    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "examples": [
                {
                    "document_id": DOC_ID,
                    "version_id": VERSION_ID,
                    "job_id": "0192f3a4-7b1e-7b90-a0d1-2c3e4f506172",
                    "status": "uploaded",
                    "deduplicated": False,
                    "links": {
                        "document": f"/v1/documents/{DOC_ID}",
                        "processing": f"/v1/versions/{VERSION_ID}/processing",
                    },
                }
            ]
        },
    )


class VersionOut(Model):
    id: uuid.UUID
    version_no: int
    filename: str
    mime: str
    size_bytes: int
    sha256: str
    page_count: int | None = None
    status: str
    quality: dict[str, Any] | None = None
    error_code: str | None = None
    created_at: str | None = None
    ready_at: str | None = None


_VERSION_EXAMPLE: dict[str, Any] = {
    "id": VERSION_ID,
    "version_no": 1,
    "filename": "master-services-agreement.pdf",
    "mime": "application/pdf",
    "size_bytes": 482113,
    "sha256": "9f2c4e1a7b3d5c60e8f1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f7",
    "page_count": 12,
    "status": "enriched",
    "quality": {"score": 0.97},
    "error_code": None,
    "created_at": TS,
    "ready_at": TS,
}


class DocumentListItem(Model):
    id: uuid.UUID
    title: str
    status: str
    category: str | None = None
    tags: list[str] | None = None
    summary_snippet: str | None = None
    created_at: str | None = None
    version: VersionOut


class DocumentList(Model):
    items: list[DocumentListItem]
    next_cursor: str | None = None
    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "examples": [
                {
                    "items": [
                        {
                            "id": DOC_ID,
                            "title": "Master Services Agreement",
                            "status": "enriched",
                            "category": "contract",
                            "tags": ["msa", "services"],
                            "summary_snippet": "Services agreement between Acme and Globex.",
                            "created_at": TS,
                            "version": _VERSION_EXAMPLE,
                        }
                    ],
                    "next_cursor": None,
                }
            ]
        },
    )


class DocumentDetail(Model):
    id: uuid.UUID
    title: str
    category: str | None = None
    tags: list[str] | None = None
    created_at: str | None = None
    status: str | None = None
    current_version_id: uuid.UUID | None = None
    quality: dict[str, Any] | None = None
    versions: list[VersionOut]
    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "examples": [
                {
                    "id": DOC_ID,
                    "title": "Master Services Agreement",
                    "category": "contract",
                    "tags": ["msa"],
                    "created_at": TS,
                    "status": "enriched",
                    "current_version_id": VERSION_ID,
                    "quality": {"score": 0.97},
                    "versions": [_VERSION_EXAMPLE],
                }
            ]
        },
    )


class BriefOut(Model):
    status: str = Field(description="`pending`, `ready`, `partial` or `failed`")
    version_id: uuid.UUID | None = None
    document_type: str | None = None
    category: str | None = None
    tags: list[str] = []
    summary: str | None = None
    suggested_questions: list[str] = []
    facts: list[dict[str, Any]] = Field(default=[], description="Cited facts, each `verified` or not")
    model: str | None = None
    created_at: str | None = None
    model_config = ConfigDict(
        extra="allow",
        protected_namespaces=(),
        json_schema_extra={
            "examples": [
                {
                    "status": "ready",
                    "version_id": VERSION_ID,
                    "document_type": "contract",
                    "category": "contract",
                    "tags": ["msa"],
                    "summary": "Services agreement between Acme and Globex.",
                    "suggested_questions": ["What is the termination notice period?"],
                    "facts": [],
                    "model": "balanced-model",
                    "created_at": TS,
                }
            ]
        },
    )


class SummaryOut(Model):
    id: uuid.UUID
    document_id: uuid.UUID
    version_id: uuid.UUID
    params: dict[str, Any] | None = None
    created_at: str | None = None
    status: str = Field(description="`pending`, `ready` or `failed`")
    segments: list[dict[str, Any]] | None = Field(default=None, description="Ready only: cited segments")
    word_count: int | None = None
    reasons: list[str] | None = None
    model: str | None = None
    error_code: str | None = None
    poll: str | None = Field(default=None, description="Pending only: URL to poll")
    model_config = ConfigDict(
        extra="allow",
        protected_namespaces=(),
        json_schema_extra={
            "examples": [
                {
                    "id": "0192f3a6-1a01-7a10-b2c3-4d5e6f708192",
                    "document_id": DOC_ID,
                    "version_id": VERSION_ID,
                    "params": {"length": "short"},
                    "created_at": TS,
                    "status": "pending",
                    "poll": f"/v1/documents/{DOC_ID}/summaries/0192f3a6-1a01-7a10-b2c3-4d5e6f708192",
                }
            ]
        },
    )


# --- versions and chunks -----------------------------------------------------------------------------------
class JobState(Model):
    status: str
    current_stage: str | None = None
    attempt_count: int
    error_code: str | None = None


class StageEvent(Model):
    stage: str
    status: str
    attempt: int
    duration_ms: int | None = None
    at: str


class PageFlags(Model):
    page_no: int
    flags: Any = None
    quality_score: float | None = None
    text_chars: int | None = None


class ProcessingOut(Model):
    version_id: uuid.UUID
    status: str
    error_code: str | None = None
    quality: dict[str, Any] | None = None
    job: JobState | None = None
    stages: list[StageEvent]
    pages: list[PageFlags]
    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "examples": [
                {
                    "version_id": VERSION_ID,
                    "status": "enriched",
                    "error_code": None,
                    "quality": {"score": 0.97},
                    "job": {
                        "status": "done",
                        "current_stage": "brief",
                        "attempt_count": 1,
                        "error_code": None,
                    },
                    "stages": [
                        {"stage": "extract", "status": "done", "attempt": 1, "duration_ms": 840, "at": TS}
                    ],
                    "pages": [{"page_no": 1, "flags": [], "quality_score": 0.98, "text_chars": 2210}],
                }
            ]
        },
    )


class PageLayoutOut(Model):
    version_id: uuid.UUID
    page: int
    width: float
    height: float
    glyphs: list[Any] = Field(description="Glyph boxes in page coordinates (design §6.4)")
    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "examples": [
                {
                    "version_id": VERSION_ID,
                    "page": 1,
                    "width": 612.0,
                    "height": 792.0,
                    "glyphs": [[72.0, 90.5, 80.1, 101.2]],
                }
            ]
        },
    )


class ChunkVersion(Model):
    id: uuid.UUID
    version_no: int
    filename: str


class ChunkOut(Model):
    id: uuid.UUID
    document_id: uuid.UUID
    ordinal: int
    kind: str
    text: str = Field(description="`chunks.original_text`: the only citable text")
    sentences: list[Any]
    page_start: int = Field(description="0 for flow documents (DOCX/TXT/MD): no pages")
    page_end: int
    section_path: list[str]
    token_count: int
    prev_chunk_id: uuid.UUID | None = Field(
        default=None, description="Previous chunk by ordinal in the same version; `null` for the first"
    )
    next_chunk_id: uuid.UUID | None = Field(
        default=None, description="Next chunk by ordinal in the same version; `null` for the last"
    )
    version: ChunkVersion
    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "examples": [
                {
                    "id": CHUNK_ID,
                    "document_id": DOC_ID,
                    "ordinal": 4,
                    "kind": "paragraph",
                    "text": "Either party may terminate this Agreement on thirty (30) days written notice.",
                    "sentences": [[0, 77]],
                    "page_start": 3,
                    "page_end": 3,
                    "section_path": ["8", "Termination"],
                    "token_count": 17,
                    "prev_chunk_id": "0192f3a4-0000-7000-8000-0000000000c3",
                    "next_chunk_id": None,
                    "version": {
                        "id": VERSION_ID,
                        "version_no": 1,
                        "filename": "master-services-agreement.pdf",
                    },
                }
            ]
        },
    )


# --- chat --------------------------------------------------------------------------------------------------
class SessionOut(Model):
    id: uuid.UUID
    title: str | None = None
    document_ids: list[uuid.UUID] | None = Field(default=None, description="`null` = all documents")
    created_at: str
    updated_at: str
    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "examples": [
                {
                    "id": SESSION_ID,
                    "title": "Termination terms",
                    "document_ids": [DOC_ID],
                    "created_at": TS,
                    "updated_at": TS,
                }
            ]
        },
    )


class SessionList(Model):
    items: list[SessionOut]
    next_cursor: str | None = None
    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "examples": [
                {
                    "items": [
                        {
                            "id": SESSION_ID,
                            "title": "Termination terms",
                            "document_ids": None,
                            "created_at": TS,
                            "updated_at": TS,
                        }
                    ],
                    "next_cursor": None,
                }
            ]
        },
    )


class MessageOut(Model):
    id: uuid.UUID
    role: str
    content: str
    created_at: str
    response_type: str | None = None
    coverage_state: str | None = None
    staleness: dict[str, Any] | None = None
    run_id: uuid.UUID | None = None
    segments: list[dict[str, Any]] = Field(
        default=[], description="Assistant answers: segments with citations"
    )


class MessageList(Model):
    items: list[MessageOut]
    next_cursor: str | None = None
    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "examples": [
                {
                    "items": [
                        {
                            "id": MESSAGE_ID,
                            "role": "assistant",
                            "content": "Either party may terminate on 30 days written notice.",
                            "created_at": TS,
                            "response_type": "answer",
                            "coverage_state": "covered",
                            "staleness": None,
                            "run_id": RUN_ID,
                            "segments": [],
                        }
                    ],
                    "next_cursor": None,
                }
            ]
        },
    )


class PostMessageOut(Model):
    run_id: uuid.UUID
    user_message_id: uuid.UUID
    status: str = Field(description="`running` or a terminal status")
    deduplicated: bool
    message: dict[str, Any] | None = Field(default=None, description="The assistant message once finished")
    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "examples": [
                {
                    "run_id": RUN_ID,
                    "user_message_id": "0192f3a5-0c10-7a01-9b02-3c4d5e6f7081",
                    "status": "running",
                    "deduplicated": False,
                    "message": None,
                }
            ]
        },
    )


class FeedbackOut(Model):
    id: uuid.UUID
    message_id: uuid.UUID
    rating: str
    reason: str | None = None
    segment_ordinal: int | None = None
    note: str | None = None
    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "examples": [
                {
                    "id": "0192f3a7-2b01-7c10-8d20-3e4f5a6b7c8d",
                    "message_id": MESSAGE_ID,
                    "rating": "down",
                    "reason": "unsupported",
                    "segment_ordinal": 0,
                    "note": None,
                }
            ]
        },
    )


# --- metrics -----------------------------------------------------------------------------------------------
class Percentiles(Model):
    p50: float | None = None
    p95: float | None = None


class FeedbackSummary(Model):
    up: int
    down: int
    by_reason: dict[str, int]


class DocumentsMetrics(Model):
    scope: str
    documents: int
    by_status: dict[str, int]
    by_mime: dict[str, int]
    needs_ocr: int
    pages: int
    chunks: Any
    storage_bytes: int
    time_to_ready_seconds: Percentiles
    enriched: int
    by_category: dict[str, int]
    brief_cost_usd: dict[str, float | None]
    facts: dict[str, Any]
    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "examples": [
                {
                    "scope": "owner",
                    "documents": 3,
                    "by_status": {"enriched": 3, "failed": 0},
                    "by_mime": {"application/pdf": 3},
                    "needs_ocr": 0,
                    "pages": 41,
                    "chunks": {"total": 210},
                    "storage_bytes": 1544021,
                    "time_to_ready_seconds": {"p50": 38.2, "p95": 71.0},
                    "enriched": 3,
                    "by_category": {"contract": 3},
                    "brief_cost_usd": {"total": 0.012, "per_document_p50": 0.004},
                    "facts": {"total": 18, "verified_ratio": 0.94},
                }
            ]
        },
    )


class ProcessingMetrics(Model):
    scope: str
    window: str
    throughput: dict[str, Any]
    stage_duration_ms: dict[str, dict[str, Any]]
    failures_by_error_code: dict[str, int]
    retries: Any
    redispatch_candidates: Any
    oldest_pending_age_seconds: float | None = None
    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "examples": [
                {
                    "scope": "owner",
                    "window": "24h",
                    "throughput": {"jobs_completed": 3, "stage_runs": 18, "jobs_per_hour": 0.125},
                    "stage_duration_ms": {"extract": {"count": 3, "p50": 840.0, "p95": 1900.0}},
                    "failures_by_error_code": {},
                    "retries": 0,
                    "redispatch_candidates": 0,
                    "oldest_pending_age_seconds": None,
                }
            ]
        },
    )


class LlmMetrics(Model):
    calls: int
    total_cost_usd: float | None = None
    cache_hit_ratio: float | None = None
    tokens: list[dict[str, Any]]
    cost_by_purpose: dict[str, dict[str, Any]]


class ChatMetrics(Model):
    scope: str
    window: str
    turns: int
    outcomes: dict[str, int]
    coverage: dict[str, int]
    latency_ms: Percentiles
    ttft_ms: Percentiles
    cost_per_turn_usd: Percentiles
    degraded: dict[str, int]
    feedback: FeedbackSummary
    llm: LlmMetrics
    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "examples": [
                {
                    "scope": "owner",
                    "window": "24h",
                    "turns": 12,
                    "outcomes": {"answer": 11, "abstain": 1},
                    "coverage": {"covered": 10, "partial": 1, "none": 1},
                    "latency_ms": {"p50": 5200.0, "p95": 9100.0},
                    "ttft_ms": {"p50": 1400.0, "p95": 2600.0},
                    "cost_per_turn_usd": {"p50": 0.004, "p95": 0.009},
                    "degraded": {},
                    "feedback": {"up": 3, "down": 1, "by_reason": {"unsupported": 1}},
                    "llm": {
                        "calls": 40,
                        "total_cost_usd": 0.061,
                        "cache_hit_ratio": 0.31,
                        "tokens": [],
                        "cost_by_purpose": {"generate": {"calls": 12, "cost_usd": 0.05}},
                    },
                }
            ]
        },
    )


# --- health ------------------------------------------------------------------------------------------------
class Liveness(BaseModel):
    status: str = Field(examples=["ok"])
    model_config = ConfigDict(json_schema_extra={"examples": [{"status": "ok"}]})
