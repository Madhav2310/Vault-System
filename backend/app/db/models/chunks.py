import uuid

from pgvector.sqlalchemy import HALFVEC
from sqlalchemy import Computed, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base, CreatedAt, Json

EMBEDDING_DIM = 1536


class Chunk(CreatedAt, Base):
    """Hash-partitioned by owner_id (32 partitions, created by migration 0001). PK is (owner_id, id)."""

    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("owner_id", "version_id", "ordinal", name="uq_chunks_owner_version_ordinal"),
        {"postgresql_partition_by": "HASH (owner_id)"},
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"))
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_versions.id"))
    ordinal: Mapped[int]
    kind: Mapped[str]
    original_text: Mapped[str]
    structural_prefix: Mapped[str | None]
    contextual_prefix: Mapped[str | None]
    embedding_text: Mapped[str]
    page_start: Mapped[int]
    page_end: Mapped[int]
    section_path: Mapped[list[str]] = mapped_column(default=list, server_default="{}")
    sentences: Mapped[Json]
    spans: Mapped[Json]
    token_count: Mapped[int]
    content_hash: Mapped[str]
    embedding: Mapped[list[float] | None] = mapped_column(HALFVEC(EMBEDDING_DIM))
    embedding_model: Mapped[str | None]
    tsv_en: Mapped[str | None] = mapped_column(
        TSVECTOR, Computed("to_tsvector('english'::regconfig, embedding_text)", persisted=True)
    )
    tsv_ids: Mapped[str | None] = mapped_column(TSVECTOR)
