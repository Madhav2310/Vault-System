"""Run async DB work from (sync, prefork) Celery tasks.

Each task invocation gets its own event loop and a NullPool engine: async engines and their connections are
bound to one loop, so sharing the API's cached engine across `asyncio.run` calls would break.
"""

import asyncio
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.settings import get_settings

SessionFactory = async_sessionmaker[AsyncSession]


def run_db[T](fn: Callable[[SessionFactory], Awaitable[T]]) -> T:
    async def main() -> T:
        engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
        try:
            return await fn(async_sessionmaker(engine, expire_on_commit=False))
        finally:
            await engine.dispose()

    return asyncio.run(main())
