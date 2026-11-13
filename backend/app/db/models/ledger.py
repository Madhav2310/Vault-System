"""Evidence ledger: rag_runs + run_versions + run_steps + retrieval_candidates + answer_segments
+ segment_evidence + llm_calls (design §11). chunk_id columns have no FK (chunks PK is (owner_id, id))."""

import datetime as dt
import uuid
from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base, CreatedAt, Json, pk


class RagRun(CreatedAt, Base):
    __tablename__ = "rag_runs"
    id: Mapped[uuid.UUID] = pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    session_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("chat_sessions.id"))
    user_message_id: Mapped[uuid.UUID | None]
    assistant_message_id: Mapped[uuid.UUID | None]
    matrix_cell_id: Mapped[uuid.UUID | None]
    replay_of_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("rag_runs.id"))
    trace_id: Mapped[str | None]
    route: Mapped[str]
    mode: Mapped[str]
    original_query: Mapped[str]
    standalone_query: Mapped[str | None]
    analysis: Mapped[Json | None]
    coverage: Mapped[Json | None]
    conflicts: Mapped[Json | None]
    status: Mapped[str]
    reason_codes: Mapped[list[str]] = mapped_column(default=list, server_default="{}")
    degraded: Mapped[list[str]] = mapped_column(default=list, server_default="{}")
    limits: Mapped[Json | None]
    config: Mapped[Json | None]
    llm_calls_used: Mapped[int] = mapped_column(default=0, server_default="0")
    input_tokens: Mapped[int] = mapped_column(default=0, server_default="0")
    output_tokens: Mapped[int] = mapped_column(default=0, server_default="0")
    cache_read_tokens: Mapped[int] = mapped_column(default=0, server_default="0")
    cost_usd: Mapped[Decimal] = mapped_column(default=Decimal(0), server_default="0")
    latency_ms: Mapped[int | None]
    ttft_ms: Mapped[int | None]
    finished_at: Mapped[dt.datetime | None]


class RunVersion(CreatedAt, Base):
    __tablename__ = "run_versions"
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rag_runs.id"), primary_key=True)
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_versions.id"), primary_key=True)


class RunStep(CreatedAt, Base):
    __tablename__ = "run_steps"
    __table_args__ = (UniqueConstraint("run_id", "seq", name="uq_run_steps_run_seq"),)
    id: Mapped[uuid.UUID] = pk()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rag_runs.id"))
    seq: Mapped[int]
    node: Mapped[str]
    status: Mapped[str]
    started_at: Mapped[dt.datetime]
    duration_ms: Mapped[int | None]
    decision: Mapped[Json | None]
    reason_code: Mapped[str | None]
    error_code: Mapped[str | None]


class RetrievalCandidate(CreatedAt, Base):
    __tablename__ = "retrieval_candidates"
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rag_runs.id"), primary_key=True)
    attempt: Mapped[int] = mapped_column(primary_key=True)
    chunk_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    vector_rank: Mapped[int | None]
    vector_distance: Mapped[float | None]
    lexical_rank: Mapped[int | None]
    lexical_score: Mapped[float | None]
    id_match: Mapped[bool] = mapped_column(default=False, server_default="false")
    rrf_score: Mapped[float | None]
    rerank_score: Mapped[float | None]
    selected: Mapped[bool] = mapped_column(default=False, server_default="false")
    expanded_from: Mapped[uuid.UUID | None]
    result_index: Mapped[int | None]


class AnswerSegment(CreatedAt, Base):
    __tablename__ = "answer_segments"
    __table_args__ = (
        UniqueConstraint("run_id", "ordinal", name="uq_answer_segments_run_ordinal"),
        CheckConstraint(
            "verification IS NULL OR verification IN ('supported', 'partial', 'unsupported', 'unchecked')",
            name="ck_answer_segments_verification",
        ),
    )
    id: Mapped[uuid.UUID] = pk()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rag_runs.id"))
    message_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("chat_messages.id"))
    ordinal: Mapped[int]
    text: Mapped[str]
    support: Mapped[str | None]
    verification: Mapped[str | None]  # support check (S17.09); NULL = no check ran
    verification_reason: Mapped[str | None]


class SegmentEvidence(CreatedAt, Base):
    """sentence_end is exclusive: [sentence_start, sentence_end), mirroring the API's block range."""

    __tablename__ = "segment_evidence"
    __table_args__ = (CheckConstraint("sentence_start < sentence_end", name="ck_segment_evidence_half_open"),)
    segment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("answer_segments.id"), primary_key=True)
    chunk_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    sentence_start: Mapped[int] = mapped_column(primary_key=True)
    sentence_end: Mapped[int]
    cited_text: Mapped[str]


class LlmCall(CreatedAt, Base):
    __tablename__ = "llm_calls"
    id: Mapped[uuid.UUID] = pk()
    owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("rag_runs.id"))
    job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("processing_jobs.id"))
    purpose: Mapped[str]
    lane: Mapped[str]
    provider: Mapped[str]
    role: Mapped[str | None]  # fast | balanced | deep | flagship; NULL for embeddings
    model: Mapped[str]  # requested
    resolved_model: Mapped[str | None]  # what the API reports having served
    prompt_id: Mapped[str | None]
    prompt_version: Mapped[int | None]
    attempt: Mapped[int]
    input_tokens: Mapped[int] = mapped_column(default=0, server_default="0")
    output_tokens: Mapped[int] = mapped_column(default=0, server_default="0")
    cache_read_tokens: Mapped[int] = mapped_column(default=0, server_default="0")
    cache_write_tokens: Mapped[int] = mapped_column(default=0, server_default="0")
    cost_usd: Mapped[Decimal] = mapped_column(default=Decimal(0), server_default="0")
    latency_ms: Mapped[int | None]
    ttft_ms: Mapped[int | None]
    status: Mapped[str]
    error_type: Mapped[str | None]
