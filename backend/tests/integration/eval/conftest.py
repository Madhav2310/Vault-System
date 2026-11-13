"""Eval integration fixtures. `tracker` records run ids so teardown deletes only this test's eval rows and
the chat sessions a multi-turn test opened, before the shared `make_owner` removes users."""

from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.support.evalkit import Tracker


@pytest.fixture
async def tracker(sm: async_sessionmaker[AsyncSession], make_owner: object) -> AsyncIterator[Tracker]:
    # depends on make_owner so this teardown runs first: chat_sessions reference users
    t = Tracker()
    yield t
    async with sm() as s:
        p: dict[str, Any] = {"ids": t.run_ids}
        await s.execute(text("DELETE FROM eval_results WHERE eval_run_id = ANY(:ids)"), p)
        await s.execute(text("DELETE FROM eval_runs WHERE id = ANY(:ids)"), p)
        await s.execute(
            text("DELETE FROM chat_sessions WHERE owner_id = ANY(:o)"), {"o": t.session_owner_ids}
        )
        await s.commit()
