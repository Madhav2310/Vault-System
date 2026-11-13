"""Fixtures for the metrics endpoint tests: owners (optionally admin) with derived rows cleaned up."""

import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest
from sqlalchemy import text

from tests.support.docs import clear_owner_rows


@pytest.fixture
async def mk_owner(make_owner: Callable[[], Any], sm: Any) -> AsyncIterator[Callable[..., Any]]:
    """`await mk_owner(admin=False)`. Teardown removes chat/ledger rows before `make_owner`'s own cleanup."""
    made: list[uuid.UUID] = []

    async def mk(*, admin: bool = False) -> Any:
        o = await make_owner()
        made.append(o.id)
        if admin:
            async with sm() as s:
                await s.execute(text("UPDATE users SET is_admin = true WHERE id = :i"), {"i": o.id})
                await s.commit()
        return o

    yield mk
    async with sm() as s:
        p = {"ids": made}
        await s.execute(text("DELETE FROM llm_calls WHERE owner_id = ANY(:ids)"), p)
        await s.execute(text("DELETE FROM chat_messages WHERE owner_id = ANY(:ids)"), p)
        await s.commit()
    await clear_owner_rows(sm, made)  # rag_runs, run_versions, chunks, pages, insights
    async with sm() as s:
        await s.execute(text("DELETE FROM chat_sessions WHERE owner_id = ANY(:ids)"), {"ids": made})
        await s.commit()
