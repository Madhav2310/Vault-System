"""S2.08 rerank client: reordering, top_n, degradation (timeout, 503, down, bad response), request bounds,
and one run against the real reranker service when it is reachable."""

import asyncio
import json
import os
import uuid
from collections.abc import Callable

import httpx
import pytest

from app.retrieval.rerank import (
    DEGRADED_NO_RERANK,
    MAX_PASSAGE_CHARS,
    MAX_PASSAGES,
    RerankClient,
    RerankPassage,
)


def _passages(n: int) -> list[RerankPassage]:
    return [RerankPassage(uuid.UUID(int=i + 1), f"passage {i}") for i in range(n)]


def _client(transport: httpx.MockTransport, timeout_ms: int = 800) -> RerankClient:
    return RerankClient("http://reranker.test", timeout_ms, transport=transport)


def _ok(scores_for: Callable[[str], float]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        return httpx.Response(200, json={"scores": [scores_for(p) for p in body["passages"]], "model": "m"})

    return httpx.MockTransport(handler)


async def test_reorders_top_n() -> None:
    # the score is the number in the text, so the order is reversed: passage 11 first
    client = _client(_ok(lambda p: float(p.split()[-1])))
    res = await client.rerank("q", _passages(12), top_n=8)
    assert not res.degraded and res.degraded_reason is None and res.failure is None
    assert [i.input_index for i in res.items] == [11, 10, 9, 8, 7, 6, 5, 4]  # top_n=8 kept, best first
    assert [i.score for i in res.items] == [11.0, 10.0, 9.0, 8.0, 7.0, 6.0, 5.0, 4.0]
    assert [i.chunk_id for i in res.items] == [uuid.UUID(int=i + 1) for i in (11, 10, 9, 8, 7, 6, 5, 4)]
    assert res.scores == [float(i) for i in range(12)]  # aligned to the input order
    assert res.model == "m"
    default = await client.rerank("q", _passages(12))
    assert len(default.items) == 8
    # equal scores keep the input (fusion) order
    flat = await _client(_ok(lambda p: 1.0)).rerank("q", _passages(5), top_n=3)
    assert [i.input_index for i in flat.items] == [0, 1, 2]
    await client.aclose()


async def test_timeout_degrades() -> None:
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(2)
        return httpx.Response(200, json={"scores": [0.0], "model": "m"})

    client = _client(httpx.MockTransport(slow), timeout_ms=50)
    res = await client.rerank("q", _passages(12), top_n=8)  # must not raise
    assert res.degraded and res.degraded_reason == DEGRADED_NO_RERANK == "no_rerank"
    assert res.failure == "timeout"
    assert [i.input_index for i in res.items] == list(range(8))  # RRF (input) order kept, truncated to top_n
    assert all(i.score is None for i in res.items) and res.scores == [None] * 12

    def read_timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    res = await _client(httpx.MockTransport(read_timeout)).rerank("q", _passages(3))
    assert res.degraded and res.failure == "timeout"


async def test_down_degrades() -> None:
    def refused(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    res = await _client(httpx.MockTransport(refused)).rerank("q", _passages(10), top_n=8)
    assert res.degraded and res.degraded_reason == "no_rerank" and res.failure == "connect_error"
    assert [i.input_index for i in res.items] == list(range(8))

    def overloaded(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"code": "overloaded", "retryable": True})

    res = await _client(httpx.MockTransport(overloaded)).rerank("q", _passages(3))
    assert res.degraded and res.failure == "overloaded"

    for status in (500, 422, 404):
        res = await _client(httpx.MockTransport(lambda r, s=status: httpx.Response(s))).rerank(
            "q", _passages(3)
        )
        assert res.degraded and res.failure == "http_error"


async def test_bad_response_degrades() -> None:
    bodies: list[tuple[int, bytes]] = [
        (200, b"not json"),
        (200, json.dumps({"scores": [1.0]}).encode()),  # wrong length
        (200, json.dumps({"scores": "x", "model": "m"}).encode()),
        (200, json.dumps({"scores": [1.0, "a", 2.0], "model": "m"}).encode()),
        (200, json.dumps({"scores": [1.0, None, 2.0]}).encode()),
        (200, b'{"scores": [1.0, NaN, 2.0]}'),
    ]
    for status, body in bodies:
        client = _client(httpx.MockTransport(lambda r, s=status, b=body: httpx.Response(s, content=b)))
        res = await client.rerank("q", _passages(3))
        assert res.degraded and res.failure == "bad_response", body
        assert [i.input_index for i in res.items] == [0, 1, 2]


async def test_request_bounds() -> None:
    seen: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        return httpx.Response(200, json={"scores": [1.0] * len(body["passages"]), "model": "m"})

    client = _client(httpx.MockTransport(handler))
    many = [
        RerankPassage(uuid.UUID(int=i + 1), "x" * (MAX_PASSAGE_CHARS + 500)) for i in range(MAX_PASSAGES + 20)
    ]
    res = await client.rerank("q" * 5000, many, top_n=8)
    body = seen[0]
    passages = body["passages"]
    assert isinstance(passages, list) and len(passages) == MAX_PASSAGES == 100
    assert all(len(p) == MAX_PASSAGE_CHARS for p in passages)
    assert isinstance(body["query"], str) and len(body["query"]) <= 2000
    assert not res.degraded and len(res.scores) == len(many)
    assert all(s is None for s in res.scores[MAX_PASSAGES:])  # beyond the cap: not scored, not selectable
    assert all(i.input_index < MAX_PASSAGES for i in res.items)
    # nothing to rank: no request at all
    seen.clear()
    empty = await client.rerank("q", [])
    assert seen == [] and empty.items == [] and not empty.degraded


async def test_real_reranker_service() -> None:
    url = os.environ.get("RERANKER_URL", "http://localhost:8180")
    try:
        async with httpx.AsyncClient(timeout=2) as c:
            health = await c.get(f"{url}/healthz")
        health.raise_for_status()
    except httpx.HTTPError as e:
        pytest.skip(f"reranker service not reachable at {url} ({type(e).__name__}); start it with compose")
    client = RerankClient(url, 5000)  # generous: the first call may warm the model
    relevant = RerankPassage(uuid.UUID(int=1), "The tenant must pay rent on the first day of every month.")
    other = RerankPassage(uuid.UUID(int=2), "Bananas are rich in potassium and grow in tropical climates.")
    res = await client.rerank("When is rent due?", [other, relevant], top_n=2)
    assert not res.degraded, res.failure
    assert res.items[0].chunk_id == relevant.chunk_id and res.items[0].input_index == 1
    assert res.scores[1] is not None and res.scores[0] is not None and res.scores[1] > res.scores[0]
    assert res.model
    await client.aclose()
