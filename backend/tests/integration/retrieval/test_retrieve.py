"""S2.10 retrieve() facade: complete candidate fields, result cache, degradation never cached."""

import json
import os
import random
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest
import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.retrieval.config import get_config
from app.retrieval.load import load_evidence_chunks
from app.retrieval.query_embed import QueryEmbedder
from app.retrieval.rerank import RerankClient
from app.retrieval.retrieve import Retriever, result_cache_key
from app.retrieval.scope import compute_scope
from tests.support.docs import seed_document
from tests.support.llm import make_gateway
from tests.support.retrieval import seed_text_chunk
from tests.support.vectors import seed_vectors, unit_vector

SM = async_sessionmaker[AsyncSession]


@pytest.fixture
async def rds() -> AsyncIterator[aioredis.Redis]:
    client = aioredis.from_url(os.environ["REDIS_URL"])
    yield client
    await client.aclose()


def _reranker(fail: bool = False) -> RerankClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if fail:
            raise httpx.ConnectError("down", request=request)
        body = json.loads(request.content)
        return httpx.Response(
            200, json={"scores": [float(i) for i in range(len(body["passages"]))], "model": "m"}
        )

    return RerankClient("http://reranker.test", 800, transport=httpx.MockTransport(handler))


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
    r = Retriever(QueryEmbedder(gw, None), _reranker(), rds)
    async with sm() as s:
        scope = await compute_scope(s, o.id, None)
        res = await r.retrieve(s, o.id, scope, "indemnification clause")
        assert not res.degraded and not res.cache_hit
        cfg = get_config()
        sel = [c for c in res.candidates if c.selected]
        assert (
            0
            < len({c.chunk_id for c in res.candidates if c.rerank_score is not None})
            <= cfg.rerank.candidates
        )
        assert [c.chunk_id for c in sorted(sel, key=lambda c: c.result_index or 0)] == list(res.selected)
        assert [c.result_index for c in sorted(sel, key=lambda c: c.result_index or 0)] == list(
            range(len(sel))
        )
        lex = next(c for c in res.candidates if c.lexical_rank is not None)
        assert lex.lexical_score is not None and lex.rrf_score is not None
        vec = next(c for c in res.candidates if c.vector_rank is not None)
        assert vec.vector_distance is not None and 0.0 <= vec.vector_distance <= 2.0
        top = next(c for c in res.candidates if c.rerank_score is not None)
        assert top.rrf_score is not None
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
        down = Retriever(QueryEmbedder(gw, None), _reranker(fail=True), rds)
        first = await down.retrieve(s, o.id, scope, q)
        assert first.degraded == ("no_rerank",) and first.selected
        key = result_cache_key(o.id, q, [v for _, v in scope.entries], get_config().version)
        assert await rds.get(key) is None  # degraded: not cached
        up = Retriever(QueryEmbedder(gw, None), _reranker(), rds)
        a = await up.retrieve(s, o.id, scope, q)
        b = await up.retrieve(s, o.id, scope, q.upper())  # normalization: same key
        assert not a.cache_hit and b.cache_hit and b.selected == a.selected and b.candidates == a.candidates
        await rds.delete(key)
