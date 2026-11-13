"""Rerank client (design 7.3, 15): top fused candidates -> reranker service -> top_n.

Never raises to the caller. On timeout, 503/other HTTP errors, connection errors or a malformed response the
result is degraded: the input (fusion) order is kept, `degraded=True`, `degraded_reason="no_rerank"` and
`failure` holds a bounded enum (usable as a metric label) saying why. Only chunk ids and texts that the caller
passes in are sent; nothing is logged.

At most `MAX_PASSAGES` passages are sent (the service limit); passages beyond that are neither scored nor
selectable, so callers should pass the best-first fused list. Text is truncated to the service bounds.
"""

import asyncio
import math
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.settings import Settings

DEGRADED_NO_RERANK = "no_rerank"
MAX_PASSAGES = 100  # reranker service RERANKER_MAX_PASSAGES default
MAX_PASSAGE_CHARS = 8000  # reranker service RERANKER_MAX_PASSAGE_CHARS default
MAX_QUERY_CHARS = 2000  # reranker service RERANKER_MAX_QUERY_CHARS default
DEFAULT_TOP_N = 8

# Bounded set of failure kinds (`RerankResult.failure`).
FAIL_TIMEOUT = "timeout"
FAIL_OVERLOADED = "overloaded"
FAIL_CONNECT = "connect_error"
FAIL_HTTP = "http_error"
FAIL_BAD_RESPONSE = "bad_response"


@dataclass(frozen=True)
class RerankPassage:
    chunk_id: uuid.UUID
    text: str


@dataclass(frozen=True)
class RerankedItem:
    chunk_id: uuid.UUID
    input_index: int  # position in the passages given to `rerank`
    score: float | None  # None when degraded


@dataclass(frozen=True)
class RerankResult:
    items: list[RerankedItem]  # at most top_n, best first (input order when degraded)
    scores: list[float | None]  # aligned to the input passages; None = not scored
    degraded: bool
    degraded_reason: str | None  # DEGRADED_NO_RERANK when degraded
    failure: str | None  # FAIL_* when degraded
    model: str | None


class _Failure(Exception):
    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


class RerankClient:
    def __init__(
        self, url: str, timeout_ms: int, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._timeout_s = timeout_ms / 1000
        self._http = httpx.AsyncClient(
            base_url=url.rstrip("/"), timeout=httpx.Timeout(self._timeout_s), transport=transport
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> "RerankClient":
        return cls(settings.reranker_url, settings.reranker_timeout_ms)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def rerank(
        self, query: str, passages: Sequence[RerankPassage], top_n: int = DEFAULT_TOP_N
    ) -> RerankResult:
        n = len(passages)
        if n == 0:
            return RerankResult([], [], False, None, None, None)
        sent = passages[:MAX_PASSAGES]
        try:
            scores, model = await self._score(query, [p.text for p in sent])
        except _Failure as f:
            kept = [RerankedItem(p.chunk_id, i, None) for i, p in enumerate(sent[:top_n])]
            return RerankResult(kept, [None] * n, True, DEGRADED_NO_RERANK, f.kind, None)
        order = sorted(range(len(sent)), key=lambda i: (-scores[i], i))  # ties keep fusion order
        items = [RerankedItem(sent[i].chunk_id, i, scores[i]) for i in order[:top_n]]
        full: list[float | None] = [*scores, *([None] * (n - len(sent)))]
        return RerankResult(items, full, False, None, None, model)

    async def _score(self, query: str, texts: list[str]) -> tuple[list[float], str | None]:
        payload = {"query": query[:MAX_QUERY_CHARS], "passages": [t[:MAX_PASSAGE_CHARS] for t in texts]}
        try:
            async with asyncio.timeout(self._timeout_s):  # total deadline, on top of httpx's per-phase ones
                resp = await self._http.post("/rerank", json=payload)
        except (TimeoutError, httpx.TimeoutException) as e:
            raise _Failure(FAIL_TIMEOUT) from e
        except Exception as e:  # never raise to the caller: connection refused, DNS, protocol, bad URL
            raise _Failure(FAIL_CONNECT) from e
        if resp.status_code == 503:
            raise _Failure(FAIL_OVERLOADED)
        if resp.status_code != 200:
            raise _Failure(FAIL_HTTP)
        return _parse(resp, len(texts))


def _parse(resp: httpx.Response, expected: int) -> tuple[list[float], str | None]:
    try:
        body: Any = resp.json()
        raw = body["scores"]
        model = body.get("model")
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        raise _Failure(FAIL_BAD_RESPONSE) from e
    if not isinstance(raw, list) or len(raw) != expected:
        raise _Failure(FAIL_BAD_RESPONSE)
    scores: list[float] = []
    for s in raw:
        if isinstance(s, bool) or not isinstance(s, int | float) or not math.isfinite(s):
            raise _Failure(FAIL_BAD_RESPONSE)
        scores.append(float(s))
    return scores, model if isinstance(model, str) else None
