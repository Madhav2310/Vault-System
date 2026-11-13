"""Fixtures for the insights API tests: the shared `api` app plus a stand-in for the summary task."""

import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import get_db, get_enqueue, get_store, get_turn_runner
from app.insights.summaries import get_summary_enqueue
from app.main import create_app


class SummaryEnqueueRecorder:
    """Stands in for Celery: records summary ids instead of sending the task to Redis."""

    def __init__(self) -> None:
        self.ids: list[uuid.UUID] = []
        self.fail = False

    async def __call__(self, summary_id: uuid.UUID) -> None:
        if self.fail:
            raise ConnectionError("broker down")
        self.ids.append(summary_id)


@pytest.fixture
def summary_queue() -> SummaryEnqueueRecorder:
    return SummaryEnqueueRecorder()


@pytest.fixture
async def api(  # overrides the shared fixture to also stub the summary enqueue
    sm: async_sessionmaker[AsyncSession],
    store: object,
    enqueued: object,
    summary_queue: SummaryEnqueueRecorder,
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
