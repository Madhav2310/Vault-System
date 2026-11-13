"""Core integration fixtures: owners whose chat rows are removed before the shared teardown deletes users."""

import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


@pytest.fixture
async def make_owner(
    make_owner: Callable[[], Any], sm: async_sessionmaker[AsyncSession]
) -> AsyncIterator[Callable[[], Any]]:
    made: list[uuid.UUID] = []

    async def mk() -> Any:
        o = await make_owner()
        made.append(o.id)
        return o

    yield mk
    async with sm() as s:
        for t in ("rag_runs", "chat_messages", "chat_sessions"):
            await s.execute(text(f"DELETE FROM {t} WHERE owner_id = ANY(:ids)"), {"ids": made})
        await s.commit()
