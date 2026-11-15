"""A Redis that accepts connections but never answers (stalled, paused or partitioned) must cost the
query-embedding and retrieval caches a short bounded wait, not redis-py's default 5 s per command."""

import asyncio
import pathlib
import time
from collections.abc import AsyncIterator

import pytest

from app.retrieval.query_embed import CACHE_REDIS_TIMEOUT_S, QueryEmbedder, cache_client
from tests.support.llm import make_gateway


@pytest.fixture
async def stalled_redis_url() -> AsyncIterator[str]:
    async def never_answer(_r: asyncio.StreamReader, _w: asyncio.StreamWriter) -> None:
        await asyncio.sleep(3600)

    server = await asyncio.start_server(never_answer, "127.0.0.1", 0)
    yield f"redis://127.0.0.1:{server.sockets[0].getsockname()[1]}/0"
    server.close()


async def test_stalled_redis_bypasses_query_cache_quickly(
    tmp_path: pathlib.Path, stalled_redis_url: str
) -> None:
    gw, prov, _, _ = make_gateway(tmp_path)
    rds = cache_client(stalled_redis_url)
    try:
        t = time.monotonic()
        r = await asyncio.wait_for(QueryEmbedder(gw, rds).embed("payment terms", owner_id=None), 5)
        took = time.monotonic() - t
    finally:
        await rds.aclose()
    assert r.vector is not None and not r.cache_hit and len(prov.embed_calls) == 1
    assert took < 4 * CACHE_REDIS_TIMEOUT_S + 0.5, took  # one get and one set, each cut at the timeout


def test_turn_deps_use_the_bounded_cache_client() -> None:
    from app.api import deps

    deps._turn_deps.cache_clear()
    try:
        kwargs = deps._turn_deps().retriever._redis.connection_pool.connection_kwargs  # type: ignore[union-attr]
    finally:
        deps._turn_deps.cache_clear()
    assert kwargs["socket_timeout"] == CACHE_REDIS_TIMEOUT_S
    assert kwargs["socket_connect_timeout"] == CACHE_REDIS_TIMEOUT_S
