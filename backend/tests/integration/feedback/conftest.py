"""Feedback fixtures: owners whose feedback rows are removed before the ledger teardown."""

import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.support.ledger_env import ledger_owners

__all__ = ["ledger_owners"]


@pytest.fixture
async def owners(
    make_owner: Callable[[], Any], sm: async_sessionmaker[AsyncSession]
) -> AsyncIterator[Callable[[], Any]]:
    """Copy of the chat `owners` fixture (tests/integration/chat/conftest.py is not visible here)."""
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


@pytest.fixture
async def fb_owners(
    ledger_owners: Callable[[], Any], sm: async_sessionmaker[AsyncSession]
) -> AsyncIterator[Callable[[], Any]]:
    made: list[uuid.UUID] = []

    async def mk() -> Any:
        o = await ledger_owners()
        made.append(o.id)
        return o

    yield mk
    async with sm() as s:  # before the ledger teardown removes the runs these rows reference
        await s.execute(text("DELETE FROM feedback WHERE owner_id = ANY(:ids)"), {"ids": made})
        await s.commit()
