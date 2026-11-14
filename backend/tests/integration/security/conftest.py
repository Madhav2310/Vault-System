"""Security-suite fixtures: the shared app plus a stand-in for the summary task, and owners whose chat, ledger
and feedback rows are removed before the shared `make_owner` teardown deletes the users."""

import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import get_db, get_enqueue, get_store, get_turn_runner
from app.insights.summaries import get_summary_enqueue
from app.main import create_app
from tests.support.ledger_env import ledger_owners

__all__ = ["ledger_owners"]


class SummaryQueue:
    def __init__(self) -> None:
        self.ids: list[uuid.UUID] = []

    async def __call__(self, summary_id: uuid.UUID) -> None:
        self.ids.append(summary_id)


@pytest.fixture
def summary_queue() -> SummaryQueue:
    return SummaryQueue()


@pytest.fixture
async def api(
    sm: async_sessionmaker[AsyncSession], store: object, enqueued: object, summary_queue: SummaryQueue
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()

    async def _db() -> AsyncIterator[AsyncSession]:
        async with sm() as s:
            yield s

    async def _no_turn(*_a: object) -> None:
        return None

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_enqueue] = lambda: enqueued
    app.dependency_overrides[get_summary_enqueue] = lambda: summary_queue
    app.dependency_overrides[get_turn_runner] = lambda: _no_turn
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=60) as c:
        yield c


@pytest.fixture
async def owners(
    make_owner: Callable[[], Any], sm: async_sessionmaker[AsyncSession]
) -> AsyncIterator[Callable[[], Any]]:
    """Owners whose run, message and session rows go before the user teardown (copy of the chat fixture)."""
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
async def sec_owners(
    ledger_owners: Callable[[], Any], sm: async_sessionmaker[AsyncSession]
) -> AsyncIterator[Callable[[], Any]]:
    """Owners with ledger rows; their feedback goes first (it references answer segments)."""
    made: list[uuid.UUID] = []

    async def mk() -> Any:
        o = await ledger_owners()
        made.append(o.id)
        return o

    yield mk
    async with sm() as s:
        await s.execute(text("DELETE FROM feedback WHERE owner_id = ANY(:ids)"), {"ids": made})
        await s.commit()
