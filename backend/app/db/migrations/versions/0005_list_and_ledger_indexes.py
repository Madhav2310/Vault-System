"""Indexes for the session list, windowed chat metrics and per-job spend lookups.

- `chat_sessions`: the list is keyset-paginated on (created_at, id) newest first; the old (owner_id,
  updated_at) index matched no query, so every page sorted all of the owner's sessions.
- `chat_messages`: the windowed metrics filter on (owner_id, created_at).
- `llm_calls`: the brief and contextualize stages sum what earlier deliveries of a job spent (job_id).

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-15
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


# CONCURRENTLY: building an index on a large `llm_calls` or `chat_messages` must not block the ledger and
# message writes of live turns. It cannot run inside a transaction, hence the autocommit block; IF NOT EXISTS
# makes a rerun after an interrupted build safe (Postgres then leaves an INVALID index: drop it and rerun).


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_chat_sessions_owner_created "
            "ON chat_sessions (owner_id, created_at DESC, id DESC)"
        )
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_chat_sessions_owner")
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_chat_messages_owner_created "
            "ON chat_messages (owner_id, created_at)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_llm_calls_job "
            "ON llm_calls (job_id) WHERE job_id IS NOT NULL"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_chat_sessions_owner "
            "ON chat_sessions (owner_id, updated_at)"
        )
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_llm_calls_job")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_chat_messages_owner_created")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_chat_sessions_owner_created")
