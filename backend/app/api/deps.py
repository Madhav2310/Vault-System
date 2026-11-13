"""Shared FastAPI dependencies."""

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from functools import lru_cache
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import get_settings
from app.db.session import get_sessionmaker
from app.ingestion.jobs import Enqueue, celery_enqueue
from app.storage.base import ObjectStore
from app.storage.factory import get_object_store

if TYPE_CHECKING:
    from app.chat.turn import TurnDeps

TurnRunner = Callable[[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID, str, str], Awaitable[None]]


async def get_db() -> AsyncIterator[AsyncSession]:
    """Request-scoped session. Handlers must not hold it across LLM calls or streams."""
    async with get_sessionmaker()() as session:
        yield session


def get_enqueue() -> Enqueue:
    return celery_enqueue()


@lru_cache(maxsize=1)
def get_store() -> ObjectStore:
    return get_object_store(get_settings())


@lru_cache(maxsize=1)
def _turn_deps() -> "TurnDeps":
    import redis.asyncio as aioredis

    from app.chat.turn import TurnDeps
    from app.llm.gateway import get_gateway
    from app.retrieval.query_embed import QueryEmbedder
    from app.retrieval.rerank import RerankClient
    from app.retrieval.retrieve import Retriever

    s = get_settings()
    gw = get_gateway()
    rds = aioredis.from_url(s.redis_url)
    return TurnDeps(
        get_sessionmaker(), gw, Retriever(QueryEmbedder(gw, rds), RerankClient.from_settings(s), rds)
    )


def get_turn_runner() -> "TurnRunner":
    """FastAPI dependency: runs a started turn to completion (tests override it)."""
    from app.chat.turn import run_turn

    async def run(
        owner_id: uuid.UUID, session_id: uuid.UUID, run_id: uuid.UUID, msg_id: uuid.UUID, q: str, mode: str
    ) -> None:
        await run_turn(_turn_deps(), owner_id, session_id, run_id, msg_id, q, mode)

    return run
