"""LLM listwise rerank: one `fast` strict-schema call orders the top fused
passages. Same result type as `RerankClient`, and it never raises.

The model sees the question and up to `MAX_PASSAGES` numbered focus windows (each capped at
`MAX_PASSAGE_CHARS`), and returns passage numbers only. Code repairs the list: out-of-range and duplicate
numbers are dropped, passages the model left out are appended in fusion order. An empty or invalid answer,
any provider/schema failure or a timeout keeps fusion order with `degraded=no_rerank`. Windows are used for
ranking only; they are never evidence (invariant I1). Text is data inside delimiter tags; no text is logged.
"""

import asyncio
import re
import uuid
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict

from app.core.logging import get_logger
from app.llm.errors import LlmError
from app.llm.gateway import Budget, LlmGateway
from app.retrieval.rerank import (
    DEGRADED_NO_RERANK,
    FAIL_BAD_RESPONSE,
    FAIL_HTTP,
    FAIL_TIMEOUT,
    MAX_QUERY_CHARS,
    RerankedItem,
    RerankPassage,
    RerankResult,
)

_log = get_logger("vault.retrieval.listwise")

PROMPT: tuple[str, int] = ("rerank_listwise", 1)
PURPOSE = "rerank"
MAX_PASSAGES = 30  # bounds the prompt and the cost; later passages are neither ranked nor selectable
MAX_PASSAGE_CHARS = 1200
TIMEOUT_S = 6.0
_TAGS = re.compile(r"</?\s*(passages|query|inputs)\s*>", re.IGNORECASE)


class RankingOut(BaseModel):
    """Strict-mode compatible: every field required, no defaults."""

    model_config = ConfigDict(extra="forbid")
    ranking: list[int]


def _clean(text: str, limit: int) -> str:
    return _TAGS.sub("[tag removed]", text)[:limit]


def build_prompt(query: str, texts: Sequence[str]) -> str:
    body = "\n".join(f"[{i}] {_clean(t, MAX_PASSAGE_CHARS)}" for i, t in enumerate(texts, 1))
    return f"<query>\n{_clean(query, MAX_QUERY_CHARS)}\n</query>\n<passages>\n{body}\n</passages>"


def repair_ranking(ranking: Sequence[int], n: int, top_n: int) -> list[int]:
    """0-based passage indexes, at most `top_n`: valid numbers in the model's order, then the rest in fusion
    order. An empty or entirely invalid ranking returns [] (the caller treats it as a failure)."""
    seen: set[int] = set()
    order: list[int] = []
    for num in ranking:
        if 1 <= num <= n and num not in seen:
            seen.add(num)
            order.append(num - 1)
    if not order:
        return []
    order += [i for i in range(n) if i + 1 not in seen]
    return order[:top_n]


class LlmReranker:
    def __init__(
        self, gateway: LlmGateway, *, max_passages: int = MAX_PASSAGES, timeout_s: float = TIMEOUT_S
    ) -> None:
        self._gw = gateway
        self._max = max_passages
        self._timeout_s = timeout_s

    async def rerank(
        self,
        query: str,
        passages: Sequence[RerankPassage],
        top_n: int,
        *,
        owner_id: uuid.UUID | None = None,
        run_id: uuid.UUID | None = None,
        budget: Budget | None = None,
    ) -> RerankResult:
        n = len(passages)
        if n == 0:
            return RerankResult([], [], False, None, None, None)
        sent = list(passages[: self._max])

        def fallback(kind: str) -> RerankResult:
            kept = [RerankedItem(p.chunk_id, i, None) for i, p in enumerate(sent[:top_n])]
            return RerankResult(kept, [None] * n, True, DEGRADED_NO_RERANK, kind, None)

        try:
            async with asyncio.timeout(self._timeout_s):
                res = await self._gw.call(
                    purpose=PURPOSE,
                    lane="interactive",
                    prompt=PROMPT,
                    messages=[{"role": "user", "content": build_prompt(query, [p.text for p in sent])}],
                    schema=RankingOut,
                    owner_id=owner_id,
                    run_id=run_id,
                    budget=budget,
                )
        except TimeoutError:
            return fallback(FAIL_TIMEOUT)
        except LlmError as e:
            _log.warning("listwise_rerank_failed", error_type=type(e).__name__)
            return fallback(FAIL_HTTP)
        except Exception as e:  # never raise to the caller
            _log.warning("listwise_rerank_failed", error_type=type(e).__name__)
            return fallback(FAIL_HTTP)
        assert res.output is not None
        order = repair_ranking(res.output.ranking, len(sent), top_n)
        if not order:
            return fallback(FAIL_BAD_RESPONSE)
        # Rank-only: no calibrated score exists, so the gate falls back to the vector signal (gate.top_score).
        items = [RerankedItem(sent[i].chunk_id, i, None) for i in order]
        return RerankResult(items, [None] * n, False, None, None, res.model)
