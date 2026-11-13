"""Chat integration fixtures. `owners` tears down chat rows before the shared `make_owner` removes users."""

import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


@pytest.fixture
async def owners(
    make_owner: Callable[[], Any], sm: async_sessionmaker[AsyncSession]
) -> AsyncIterator[Callable[[], Any]]:
    made: list[uuid.UUID] = []

    async def mk() -> Any:
        o = await make_owner()
        made.append(o.id)
        return o

    yield mk
    async with sm() as s:
        p = {"ids": made}
        for stmt in (
            "DELETE FROM rag_runs WHERE owner_id = ANY(:ids)",
            "DELETE FROM chat_messages WHERE owner_id = ANY(:ids)",
            "DELETE FROM chat_sessions WHERE owner_id = ANY(:ids)",
        ):
            await s.execute(text(stmt), p)
        await s.commit()
