import uuid

from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base, CreatedAt, Json, pk


class LlmCache(CreatedAt, Base):
    __tablename__ = "llm_cache"
    key: Mapped[str] = mapped_column(primary_key=True)
    output: Mapped[Json]
    model: Mapped[str]
    prompt_id: Mapped[str]
    prompt_version: Mapped[int]


class DocumentInsight(CreatedAt, Base):
    __tablename__ = "document_insights"
    __table_args__ = (
        UniqueConstraint(
            "version_id", "kind", "params_hash", "prompt_version", name="uq_document_insights_key"
        ),
    )
    id: Mapped[uuid.UUID] = pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_versions.id"))
    kind: Mapped[str]
    params_hash: Mapped[str]
    params: Mapped[Json] = mapped_column(default=dict, server_default="{}")
    content: Mapped[Json | None]
    status: Mapped[str]
    model: Mapped[str | None]
    prompt_version: Mapped[int]
