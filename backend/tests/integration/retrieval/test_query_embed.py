"""Query embedding: normalized cache key, lexical fallback, Redis outage (real Redis, unique prefix)."""

import os
import pathlib
import uuid
from collections.abc import AsyncIterator

import pytest
import redis.asyncio as aioredis

from app.llm.providers import fake
from app.retrieval.query_embed import (
    DEGRADED_LEXICAL_ONLY,
    QueryEmbedder,
    cache_key,
    normalize_query,
)
from tests.support.llm import make_gateway

MODEL = "text-embedding-3-small"


@pytest.fixture
async def rds() -> AsyncIterator[aioredis.Redis]:
    client = aioredis.from_url(os.environ["REDIS_URL"])
    yield client
    await client.aclose()


@pytest.fixture
async def prefix(rds: aioredis.Redis) -> AsyncIterator[str]:
    p = f"test:qe:{uuid.uuid4().hex}:"
    yield p
    keys = [k async for k in rds.scan_iter(match=p + "*")]
    if keys:
        await rds.delete(*keys)


def test_normalize_and_key() -> None:
    assert normalize_query("  Payment \n\tTERMS  ") == "payment terms"
    assert normalize_query(chr(0xFB01) + "x") == "fix"  # NFKC expands ligatures
    o1, o2 = uuid.uuid4(), uuid.uuid4()
    assert cache_key("p:", "a", "m1", o1) != cache_key("p:", "a", "m2", o1)
    assert cache_key("p:", "a", "m", o1) == cache_key("p:", "a", "m", o1)
    assert cache_key("p:", "a", "m", o1) != cache_key("p:", "a", "m", o2)


async def test_cache_hit_normalized(tmp_path: pathlib.Path, rds: aioredis.Redis, prefix: str) -> None:
    gw, prov, _, _ = make_gateway(tmp_path)
    qe = QueryEmbedder(gw, rds, key_prefix=prefix)
    first = await qe.embed("  Payment   TERMS ", owner_id=None)
    second = await qe.embed("payment terms", owner_id=None)
    assert len(prov.embed_calls) == 1  # the second query (other case/spacing) never hit the provider
    assert not first.cache_hit and second.cache_hit
    assert first.vector is not None and second.vector == first.vector  # miss path is quantized like the cache
    assert len(first.vector) == 1536 and first.degraded is None and not first.lexical_fallback
    assert await rds.ttl(cache_key(prefix, "payment terms", MODEL, None)) > 6 * 86400
    # the original text (not the normalized one) is what gets embedded
    assert prov.embed_calls[0][1] == ("  Payment   TERMS ",)


async def test_cache_is_per_owner(tmp_path: pathlib.Path, rds: aioredis.Redis, prefix: str) -> None:
    """One owner's cached query is never another owner's hit (a hit's timing would tell that someone else
    asked the same question)."""
    gw, prov, _, _ = make_gateway(tmp_path)
    qe = QueryEmbedder(gw, rds, key_prefix=prefix)
    a, b = uuid.uuid4(), uuid.uuid4()
    first = await qe.embed("payment terms", owner_id=a)
    other = await qe.embed("payment terms", owner_id=b)
    again = await qe.embed("payment terms", owner_id=a)
    assert not first.cache_hit and not other.cache_hit and again.cache_hit
    assert len(prov.embed_calls) == 2


async def test_failure_degrades(tmp_path: pathlib.Path, rds: aioredis.Redis, prefix: str) -> None:
    gw, _, _, _ = make_gateway(tmp_path, [fake.bad_request()])
    qe = QueryEmbedder(gw, rds, key_prefix=prefix)
    r = await qe.embed("termination notice", owner_id=None)
    assert r.vector is None and r.degraded == DEGRADED_LEXICAL_ONLY and r.lexical_fallback
    assert not r.cache_hit
    # nothing was cached for the failure: the next call (provider healthy again) embeds normally
    ok = await qe.embed("termination notice", owner_id=None)
    assert ok.vector is not None and ok.degraded is None


async def test_redis_down_bypass(tmp_path: pathlib.Path) -> None:
    gw, prov, _, _ = make_gateway(tmp_path)
    dead = aioredis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.2)
    try:
        qe = QueryEmbedder(gw, dead, key_prefix="test:qe:dead:")
        r = await qe.embed("anything", owner_id=None)
        assert r.vector is not None and len(r.vector) == 1536 and r.degraded is None and not r.cache_hit
        again = await qe.embed("anything", owner_id=None)
        assert again.vector == r.vector and len(prov.embed_calls) == 2  # uncached, still working
    finally:
        await dead.aclose()


async def test_no_redis_client_is_uncached(tmp_path: pathlib.Path) -> None:
    gw, _, _, _ = make_gateway(tmp_path)
    r = await QueryEmbedder(gw, None).embed("q", owner_id=None)
    assert r.vector is not None
