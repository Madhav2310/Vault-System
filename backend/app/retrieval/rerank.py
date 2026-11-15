"""Rerank result types shared by the retriever and the LLM listwise reranker (`listwise.py`).

A reranker never raises to the caller. On a timeout, a provider error or a malformed response the result is
degraded: the input (fusion) order is kept, `degraded=True`, `degraded_reason="no_rerank"` and `failure`
holds a bounded enum (usable as a metric label) saying why.
"""

import uuid
from dataclasses import dataclass

DEGRADED_NO_RERANK = "no_rerank"

# Bounded set of failure kinds (`RerankResult.failure`).
FAIL_TIMEOUT = "timeout"
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
