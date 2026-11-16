"""Add `role` and `resolved_model` to llm_calls.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-13

`model` stays the requested model; `resolved_model` is what the API reports having served; `role` is the
routing role (NULL for embeddings). Both are nullable.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE llm_calls ADD COLUMN role text")
    op.execute("ALTER TABLE llm_calls ADD COLUMN resolved_model text")


def downgrade() -> None:
    op.execute("ALTER TABLE llm_calls DROP COLUMN resolved_model")
    op.execute("ALTER TABLE llm_calls DROP COLUMN role")
