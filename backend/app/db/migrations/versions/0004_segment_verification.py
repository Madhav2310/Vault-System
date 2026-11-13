"""answer_segments.verification / verification_reason: the per-segment support check (design §8.5, S17.09).

`verification` is NULL when no check ran (older rows, turns without cited segments), otherwise one of
supported | partial | unsupported | unchecked. `verification_reason` is a short note (≤120 chars).

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-13
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE answer_segments ADD COLUMN verification text")
    op.execute("ALTER TABLE answer_segments ADD COLUMN verification_reason text")
    op.execute(
        "ALTER TABLE answer_segments ADD CONSTRAINT ck_answer_segments_verification "
        "CHECK (verification IS NULL OR verification IN ('supported', 'partial', 'unsupported', 'unchecked'))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE answer_segments DROP CONSTRAINT ck_answer_segments_verification")
    op.execute("ALTER TABLE answer_segments DROP COLUMN verification_reason")
    op.execute("ALTER TABLE answer_segments DROP COLUMN verification")
