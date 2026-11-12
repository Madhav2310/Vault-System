import uuid

from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base, CreatedAt, pk


class Matrix(CreatedAt, Base):
    __tablename__ = "matrices"
    id: Mapped[uuid.UUID] = pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    title: Mapped[str]
    document_ids: Mapped[list[uuid.UUID]]


class MatrixColumn(CreatedAt, Base):
    __tablename__ = "matrix_columns"
    id: Mapped[uuid.UUID] = pk()
    matrix_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("matrices.id"))
    ordinal: Mapped[int]
    question: Mapped[str]
    column_version: Mapped[int] = mapped_column(default=1, server_default="1")
    source: Mapped[str]


class MatrixCell(CreatedAt, Base):
    __tablename__ = "matrix_cells"
    __table_args__ = (
        UniqueConstraint(
            "matrix_id", "column_id", "column_version", "version_id", name="uq_matrix_cells_key"
        ),
    )
    id: Mapped[uuid.UUID] = pk()
    matrix_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("matrices.id"))
    column_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("matrix_columns.id"))
    column_version: Mapped[int]
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_versions.id"))
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("rag_runs.id"))
    status: Mapped[str]
    coverage_state: Mapped[str | None]
