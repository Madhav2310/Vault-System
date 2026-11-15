"""Drop the comparison-matrix tables and `rag_runs.matrix_cell_id`.

The matrix feature was never built: no code reads or writes `matrices`, `matrix_columns` or `matrix_cells`,
and `rag_runs.matrix_cell_id` (no foreign key) is NULL on every row. The upgrade refuses to run if any of
them holds data, so nothing is ever dropped silently. The downgrade recreates them as 0001 defined them.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

TS = "created_at timestamptz NOT NULL DEFAULT now()"


def upgrade() -> None:
    bind = op.get_bind()
    used = bind.execute(
        sa.text(
            "SELECT (SELECT count(*) FROM matrices) + (SELECT count(*) FROM matrix_columns) "
            "+ (SELECT count(*) FROM matrix_cells) "
            "+ (SELECT count(*) FROM rag_runs WHERE matrix_cell_id IS NOT NULL)"
        )
    ).scalar_one()
    if used:
        raise RuntimeError(f"matrix tables or rag_runs.matrix_cell_id hold {used} rows: not dropping them")
    op.execute("DROP TABLE matrix_cells")
    op.execute("DROP TABLE matrix_columns")
    op.execute("DROP TABLE matrices")
    op.execute("ALTER TABLE rag_runs DROP COLUMN matrix_cell_id")


def downgrade() -> None:
    op.execute("ALTER TABLE rag_runs ADD COLUMN matrix_cell_id uuid")
    op.execute(
        "CREATE TABLE matrices (id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id), "
        f"title text NOT NULL, document_ids uuid[] NOT NULL, {TS})"
    )
    op.execute("CREATE INDEX ix_matrices_owner ON matrices (owner_id, created_at)")
    op.execute(
        "CREATE TABLE matrix_columns (id uuid PRIMARY KEY, matrix_id uuid NOT NULL REFERENCES matrices(id), "
        "ordinal int NOT NULL, question text NOT NULL, column_version int NOT NULL DEFAULT 1, "
        f"source text NOT NULL, {TS})"
    )
    op.execute(
        "CREATE TABLE matrix_cells (id uuid PRIMARY KEY, matrix_id uuid NOT NULL REFERENCES matrices(id), "
        "column_id uuid NOT NULL REFERENCES matrix_columns(id), column_version int NOT NULL, "
        "version_id uuid NOT NULL REFERENCES document_versions(id), run_id uuid REFERENCES rag_runs(id), "
        f"status text NOT NULL, coverage_state text, {TS}, "
        "CONSTRAINT uq_matrix_cells_key UNIQUE (matrix_id, column_id, column_version, version_id))"
    )
