"""Shared FastAPI dependencies."""

from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import get_settings
from app.db.session import get_sessionmaker
from app.storage.base import ObjectStore
from app.storage.factory import get_object_store


async def get_db() -> AsyncIterator[AsyncSession]:
    """Request-scoped session. Handlers must not hold it across LLM calls or streams."""
    async with get_sessionmaker()() as session:
        yield session


@lru_cache(maxsize=1)
def get_store() -> ObjectStore:
    return get_object_store(get_settings())
