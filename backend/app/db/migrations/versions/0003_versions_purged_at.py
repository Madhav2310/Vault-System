"""document_versions.purged_at: a purged version that the permanent ledger still references keeps its row,
marked here (replaces a marker inside the `quality` JSON).

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-13
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE document_versions ADD COLUMN purged_at timestamptz")


def downgrade() -> None:
    op.execute("ALTER TABLE document_versions DROP COLUMN purged_at")
