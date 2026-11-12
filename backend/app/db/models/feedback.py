import uuid

from sqlalchemy import CheckConstraint, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base, CreatedAt, pk


class Feedback(CreatedAt, Base):
    __tablename__ = "feedback"
    __table_args__ = (
        UniqueConstraint(
            "owner_id", "run_id", "segment_id", "chunk_id", "kind",
            name="uq_feedback_key", postgresql_nulls_not_distinct=True,
        ),
        CheckConstraint(
            "eval_status IN ('new','exported','promoted','dismissed')", name="ck_feedback_eval_status"
        ),
    )  # fmt: skip
    id: Mapped[uuid.UUID] = pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rag_runs.id"))
    segment_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("answer_segments.id"))
    chunk_id: Mapped[uuid.UUID | None]
    kind: Mapped[str]
    note: Mapped[str | None]
    eval_status: Mapped[str] = mapped_column(default="new", server_default="new")
