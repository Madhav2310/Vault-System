"""Staleness hook (design §9.5). Called once when a version becomes `ready`, in the same transaction.

Living answers: assistant messages whose run used (`run_versions`) an earlier version of the document get
`chat_messages.stale_since = now()`. The update is idempotent (`WHERE stale_since IS NULL`: a second call
touches 0 rows) and owner-scoped. Brief, summary and matrix-cell staleness arrives with their own tasks.
"""

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def mark_stale_answers(
    session: AsyncSession, owner_id: uuid.UUID, document_id: uuid.UUID, version_id: uuid.UUID
) -> int:
    """Mark answers whose run used a version of `document_id` older than `version_id`. Returns the count."""
    res = await session.execute(
        text(
            "UPDATE chat_messages SET stale_since = now() "
            "WHERE owner_id = :o AND role = 'assistant' AND stale_since IS NULL AND run_id IN ("
            "SELECT rv.run_id FROM run_versions rv JOIN document_versions v "
            "ON v.id = rv.version_id AND v.owner_id = :o "
            "WHERE v.document_id = :d AND v.id <> :v AND v.version_no < "
            "(SELECT version_no FROM document_versions WHERE id = :v AND owner_id = :o))"
        ),
        {"o": owner_id, "d": document_id, "v": version_id},
    )
    return int(res.rowcount or 0)  # type: ignore[attr-defined]


async def on_version_ready(
    session: AsyncSession, owner_id: uuid.UUID, document_id: uuid.UUID, version_id: uuid.UUID
) -> None:
    await mark_stale_answers(session, owner_id, document_id, version_id)
