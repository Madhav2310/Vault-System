"""Retrieve() facade: complete candidate fields, result cache, degradation never cached."""

import os
import random
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest
import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.retrieval.load import load_evidence_chunks
from app.retrieval.query_embed import QueryEmbedder
from app.retrieval.scope import compute_scope
from tests.support.docs import seed_document
from tests.support.llm import make_gateway
from tests.support.retrieval import seed_text_chunk, stub_retriever
from tests.support.vectors import seed_vectors, unit_vector

SM = async_sessionmaker[AsyncSession]


@pytest.fixture
async def rds() -> AsyncIterator[aioredis.Redis]:
    client = aioredis.from_url(os.environ["REDIS_URL"])
    yield client
    await client.aclose()


async def _world(sm: SM, make_owner: Callable[[], Any]) -> tuple[Any, Any]:
    o = await make_owner()
    doc = await seed_document(sm, o.id)
    rng = random.Random(7)
    await seed_vectors(sm, o.id, doc, [unit_vector(rng) for _ in range(14)])
    await seed_text_chunk(sm, o.id, doc, "The indemnification clause survives termination.", ordinal=50)
    return o, doc


async def test_candidate_fields_complete(
    tmp_path: Any, sm: SM, make_owner: Callable[[], Any], rds: aioredis.Redis
) -> None:
    o, _doc = await _world(sm, make_owner)
    gw, _prov, _, _ = make_gateway(tmp_path)
    r = stub_retriever(QueryEmbedder(gw, None), rds)
    async with sm() as s:
        scope = await compute_scope(s, o.id, None)
        res = await r.retrieve(s, o.id, scope, "indemnification clause")
        assert not res.degraded and not res.cache_hit
        sel = [c for c in res.candidates if c.selected]
        assert all(c.rerank_score is None for c in res.candidates)  # rank only, no score
        assert [c.chunk_id for c in sorted(sel, key=lambda c: c.result_index or 0)] == list(res.selected)
        assert [c.result_index for c in sorted(sel, key=lambda c: c.result_index or 0)] == list(
            range(len(sel))
        )
        lex = next(c for c in res.candidates if c.lexical_rank is not None)
        assert lex.lexical_score is not None and lex.rrf_score is not None
        vec = next(c for c in res.candidates if c.vector_rank is not None)
        assert vec.vector_distance is not None and 0.0 <= vec.vector_distance <= 2.0
        assert all(c.rrf_score is not None for c in sel if c.expanded_from is None)
        assert all(c.version_id == scope.entries[0][1] for c in res.candidates)
        loaded = await load_evidence_chunks(s, o.id, list(res.selected))
        assert [c.chunk_id for c in loaded] == list(res.selected)
        assert (
            await load_evidence_chunks(s, uuid.uuid4(), list(res.selected)) == []
        )  # other owner sees nothing


async def test_cache_hit_and_degraded_not_cached(
    tmp_path: Any, sm: SM, make_owner: Callable[[], Any], rds: aioredis.Redis
) -> None:
    o, _doc = await _world(sm, make_owner)
    gw, _prov, _, _ = make_gateway(tmp_path)
    q = f"indemnification {uuid.uuid4().hex[:8]}"  # unique so a previous run's key cannot hit
    async with sm() as s:
        scope = await compute_scope(s, o.id, None)
        down = stub_retriever(QueryEmbedder(gw, None), rds, fail=True)
        first = await down.retrieve(s, o.id, scope, q)
        assert first.degraded == ("no_rerank",) and first.selected
        up = stub_retriever(QueryEmbedder(gw, None), rds)
        key = await up.cache_key(s, o.id, q, [v for _, v in scope.entries])
        assert key and await rds.get(key) is None  # degraded: not cached
        a = await up.retrieve(s, o.id, scope, q)
        b = await up.retrieve(s, o.id, scope, q.upper())  # normalization: same key
        assert not a.cache_hit and b.cache_hit and b.selected == a.selected and b.candidates == a.candidates
        await rds.delete(key)


async def test_rerank_passages_windowed(
    tmp_path: Any, sm: SM, make_owner: Callable[[], Any], rds: aioredis.Redis
) -> None:
    """Passages sent to the reranker are focus windows (<= 1,200 chars) of a long chunk."""
    o = await make_owner()
    doc = await seed_document(sm, o.id)
    filler = "Standard operational matters apply across regions and periods. " * 60
    body = filler + "Either party may terminate on thirty days written notice. " + filler
    await seed_text_chunk(sm, o.id, doc, body, ordinal=0)
    gw, _p, _l, _s = make_gateway(tmp_path)
    r = stub_retriever(QueryEmbedder(gw, None))
    async with sm() as s:
        scope = await compute_scope(s, o.id, None)
        await r.retrieve(s, o.id, scope, "thirty days written notice terminate")
    sent = r._llm_reranker.sent  # type: ignore[union-attr]
    assert sent and max(len(p) for p in sent) <= 1200


async def test_neighbor_versions_one_query(tmp_path: Any, sm: SM, make_owner: Callable[[], Any]) -> None:
    """Neighbors never retrieved get their versions in one query."""
    from sqlalchemy import event

    o = await make_owner()
    doc = await seed_document(sm, o.id)
    ids = [
        await seed_text_chunk(
            sm,
            o.id,
            doc,
            ("Clause covers fees and payment terms." if i in (3, 8) else f"Boilerplate recital number {i}."),
            ordinal=i,
        )
        for i in range(12)
    ]
    gw, _p, _l, _s = make_gateway(tmp_path)
    r = stub_retriever(QueryEmbedder(gw, None))
    seen: list[str] = []
    engine = sm.kw["bind"].sync_engine

    def count(conn: Any, cursor: Any, statement: str, *a: Any) -> None:
        if "SELECT id, version_id FROM chunks" in statement:
            seen.append(statement)

    scope = await compute_scope_for(sm, o.id)
    event.listen(engine, "before_cursor_execute", count)
    try:
        res = await r.retrieve(sm, o.id, scope, "fees payment terms")
    finally:
        event.remove(engine, "before_cursor_execute", count)
    assert {c.chunk_id for c in res.candidates} <= set(ids)
    expanded = [c for c in res.candidates if c.expanded_from is not None]
    assert len(expanded) >= 2
    assert len(seen) == 1


async def compute_scope_for(sm: SM, owner_id: uuid.UUID) -> Any:
    async with sm() as s:
        return await compute_scope(s, owner_id, None)
