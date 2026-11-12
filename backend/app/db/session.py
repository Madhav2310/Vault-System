"""Async engine and session factory. Sessions are short-lived: never hold one across an LLM call or stream."""

from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.settings import get_settings


@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    s = get_settings()
    return create_async_engine(
        s.database_url, pool_size=s.db_pool_size, max_overflow=s.db_max_overflow, pool_pre_ping=True
    )


@lru_cache(maxsize=1)
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def session_scope() -> AsyncIterator[AsyncSession]:
    async with get_sessionmaker()() as session:
        yield session
