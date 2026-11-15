"""Finish jobs left at the removed `contextualize` stage.

The stage ran after `brief` and wrote nothing unless contextual headers were switched on. A job still pending,
dispatched, running or failed at that stage has no next stage any more, and the sweeper could not enqueue it
(`STAGE_TASKS` has no such stage), so it is marked `succeeded`. The version's status is not touched.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-15
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "UPDATE processing_jobs SET status = 'succeeded', lease_owner = NULL, lease_expires_at = NULL, "
        "next_attempt_at = NULL, error_code = NULL, error_detail = NULL "
        "WHERE current_stage = 'contextualize' AND status NOT IN ('succeeded', 'dead')"
    )


def downgrade() -> None:
    pass  # data only: a finished job is valid for the previous code too
