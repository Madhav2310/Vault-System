"""S2.09 neighbor expansion against the database: adjacent chunk added, same section only, owner-filtered."""

import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.retrieval.expand import expand_neighbors
from tests.support.docs import Seeded, seed_document
from tests.support.retrieval import seed_text_chunk

SM = async_sessionmaker[AsyncSession]


async def _chunk(
    sm: SM, owner_id: uuid.UUID, doc: Seeded, ordinal: int, section: list[str], tokens: int = 100
) -> uuid.UUID:
    cid = await seed_text_chunk(sm, owner_id, doc, f"clause text {ordinal}", ordinal=ordinal)
    async with sm() as s:
        await s.execute(
            text("UPDATE chunks SET section_path = :p, token_count = :t WHERE owner_id = :o AND id = :i"),
            {"p": section, "t": tokens, "o": owner_id, "i": cid},
        )
        await s.commit()
    return cid


async def test_adds_neighbor(sm: SM, make_owner: Callable[[], Any]) -> None:
    o = await make_owner()
    doc = await seed_document(sm, o.id)
    c6 = await _chunk(sm, o.id, doc, 6, ["Fees"])
    c7 = await _chunk(sm, o.id, doc, 7, ["Fees"])
    c8 = await _chunk(sm, o.id, doc, 8, ["Fees"])
    async with sm() as s:
        out = await expand_neighbors(s, o.id, [c7], 1000)
    assert [e.chunk_id for e in out] == [c7, c8, c6]  # next chunk first, then the previous one
    assert [e.expanded_from for e in out] == [None, c7, c7]
    assert [e.ordinal for e in out] == [7, 8, 6]
    async with sm() as s:  # budget for exactly one more chunk
        out = await expand_neighbors(s, o.id, [c7], 200)
    assert [e.chunk_id for e in out] == [c7, c8]


async def test_same_section_only(sm: SM, make_owner: Callable[[], Any]) -> None:
    o = await make_owner()
    doc = await seed_document(sm, o.id)
    c6 = await _chunk(sm, o.id, doc, 6, ["Termination"])
    c7 = await _chunk(sm, o.id, doc, 7, ["Fees"])
    await _chunk(sm, o.id, doc, 8, ["Fees", "Late fees"])
    async with sm() as s:
        out = await expand_neighbors(s, o.id, [c7], 10_000)
    assert [e.chunk_id for e in out] == [c7]
    assert c6 not in [e.chunk_id for e in out]


async def test_owner_and_version_filtered(sm: SM, make_owner: Callable[[], Any]) -> None:
    a, b = await make_owner(), await make_owner()
    doc_a = await seed_document(sm, a.id)
    doc_a2 = await seed_document(sm, a.id)  # same owner, different version: ordinals do not cross
    doc_b = await seed_document(sm, b.id)
    a7 = await _chunk(sm, a.id, doc_a, 7, ["S"])
    await _chunk(sm, a.id, doc_a2, 8, ["S"])
    b7 = await _chunk(sm, b.id, doc_b, 7, ["S"])
    b8 = await _chunk(sm, b.id, doc_b, 8, ["S"])
    async with sm() as s:
        assert [e.chunk_id for e in await expand_neighbors(s, a.id, [a7], 10_000)] == [a7]
        assert await expand_neighbors(s, a.id, [b7], 10_000) == []  # not the owner's chunk: dropped
        assert [e.chunk_id for e in await expand_neighbors(s, b.id, [b7], 10_000)] == [b7, b8]
        assert await expand_neighbors(s, a.id, [], 10_000) == []
