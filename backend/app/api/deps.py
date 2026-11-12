"""Shared FastAPI dependencies."""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_sessionmaker


async def get_db() -> AsyncIterator[AsyncSession]:
    """Request-scoped session. Handlers must not hold it across LLM calls or streams."""
    async with get_sessionmaker()() as session:
        yield session
