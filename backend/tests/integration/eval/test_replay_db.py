"""S11.02 AC4: the replay session is a read-only transaction."""

import pytest
from eval.replay import open_read_only
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import tests.support.evalkit  # noqa: F401  (puts the repo root on sys.path)


async def test_read_only(sm: async_sessionmaker[AsyncSession]) -> None:
    async with open_read_only(sm) as s:
        assert (await s.execute(text("SELECT count(*) FROM users"))).scalar_one() >= 0
        with pytest.raises(DBAPIError):
            await s.execute(text("CREATE TEMP TABLE replay_probe (x int)"))
