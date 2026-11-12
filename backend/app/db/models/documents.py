import datetime as dt
import uuid

from sqlalchemy import BigInteger, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base, CreatedAt, Json, UpdatedAt, pk


class IdempotencyKey(CreatedAt, Base):
    __tablename__ = "idempotency_keys"
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    key: Mapped[str] = mapped_column(primary_key=True)
    request_hash: Mapped[str]
    response_status: Mapped[int | None]
    response_body: Mapped[Json | None]


class Blob(CreatedAt, Base):
    __tablename__ = "blobs"
    sha256: Mapped[str] = mapped_column(primary_key=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    mime: Mapped[str]
    storage_key: Mapped[str]
    refcount: Mapped[int] = mapped_column(default=0, server_default="0")


class Document(CreatedAt, UpdatedAt, Base):
    __tablename__ = "documents"
    id: Mapped[uuid.UUID] = pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    title: Mapped[str]
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("document_versions.id", use_alter=True, name="fk_documents_current_version")
    )
    category: Mapped[str | None]
    tags: Mapped[list[str]] = mapped_column(default=list, server_default="{}")
    deleted_at: Mapped[dt.datetime | None]


class DocumentVersion(CreatedAt, Base):
    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("document_id", "version_no", name="uq_document_versions_document_version"),
    )
    id: Mapped[uuid.UUID] = pk()
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"))
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    version_no: Mapped[int]
    filename: Mapped[str]
    mime: Mapped[str]
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(ForeignKey("blobs.sha256"))
    page_count: Mapped[int | None]
    token_count: Mapped[int | None]
    status: Mapped[str]
    quality: Mapped[Json | None]
    error_code: Mapped[str | None]
    error_detail: Mapped[str | None]
    chunker_version: Mapped[str | None]
    embedding_model: Mapped[str | None]
    ready_at: Mapped[dt.datetime | None]
    enriched_at: Mapped[dt.datetime | None]
    superseded_at: Mapped[dt.datetime | None]


class Page(CreatedAt, Base):
    __tablename__ = "pages"
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_versions.id"), primary_key=True)
    page_no: Mapped[int] = mapped_column(primary_key=True)
    width: Mapped[float]
    height: Mapped[float]
    text_chars: Mapped[int]
    quality_score: Mapped[float | None]
    flags: Mapped[list[str]] = mapped_column(default=list, server_default="{}")
