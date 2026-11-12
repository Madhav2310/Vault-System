import datetime as dt
import uuid

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base, CreatedAt, Json, UpdatedAt, pk


class ProcessingJob(CreatedAt, UpdatedAt, Base):
    __tablename__ = "processing_jobs"
    id: Mapped[uuid.UUID] = pk()
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_versions.id"), unique=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    current_stage: Mapped[str]
    status: Mapped[str]
    attempt_count: Mapped[int] = mapped_column(default=0, server_default="0")
    max_attempts: Mapped[int] = mapped_column(default=5, server_default="5")
    next_attempt_at: Mapped[dt.datetime | None]
    lease_owner: Mapped[str | None]
    lease_expires_at: Mapped[dt.datetime | None]
    last_dispatched_at: Mapped[dt.datetime | None]
    error_code: Mapped[str | None]
    error_detail: Mapped[str | None]


class JobEvent(CreatedAt, Base):
    __tablename__ = "job_events"
    id: Mapped[uuid.UUID] = pk()
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("processing_jobs.id"))
    stage: Mapped[str]
    status: Mapped[str]
    attempt: Mapped[int]
    duration_ms: Mapped[int | None]
    detail: Mapped[Json | None]
