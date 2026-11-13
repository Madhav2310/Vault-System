"""Staleness hook (design §9.5). Called once when a version becomes `ready`; the real implementation (marking
dependent briefs, summaries and matrix cells stale) arrives with S6.09. Until then it does nothing."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession


async def on_version_ready(
    session: AsyncSession, owner_id: uuid.UUID, document_id: uuid.UUID, version_id: uuid.UUID
) -> None:
    return None
