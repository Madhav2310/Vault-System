"""Helpers for custom-summary tests: scripted answer-segment replies, a seeded document, a worker run."""

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.insights.summaries import Select, run_summary
from app.llm.gateway import LlmGateway
from app.llm.providers import fake
from tests.support.brief_seed import SERVICES, ChunkSpec, Seeded, seed_ready_version


def summary_reply(*segments: tuple[str, list[str]], status: str = "answer") -> fake.FakeReply:
    return fake.structured(
        {"status": status, "segments": [{"text": t, "evidence": list(h)} for t, h in segments]}
    )


async def seed_doc(sm: async_sessionmaker[AsyncSession], owner_id: uuid.UUID, *texts: str) -> Seeded:
    return await seed_ready_version(sm, owner_id, [ChunkSpec(t) for t in (texts or (SERVICES,))])


async def no_retrieval(*_a: Any) -> list[uuid.UUID]:
    raise AssertionError("retrieval must not run for a document that fits the budget")


async def work(
    sm: async_sessionmaker[AsyncSession],
    gw: LlmGateway,
    summary_id: uuid.UUID,
    select: Select = no_retrieval,
    **kw: Any,
) -> str:
    """What the Celery task does, over the test database and a FakeProvider gateway."""
    return await run_summary(sm, gw, select, summary_id, **kw)
