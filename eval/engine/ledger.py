"""Spend ledger with a hard ceiling (research 06 §4). Spend is summed from `llm_calls.cost_usd` over the call
ids the run paid for (stages return them in `StageOutput.call_ids`; the ids are persisted in `_state.json`,
so a resumed run recomputes the same total). Batch responses are priced through the price table at the
Batch discount and written as `llm_calls` rows via the gateway's `LedgerWriter`.

`exceeded()` is checked by the runner BEFORE every uncached call or batch submission: the run stops without
making the next call once `spent >= budget`. The ceiling is therefore overshot by at most the one call (or
batch) that crossed it, never by a further one.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterable, Mapping
from decimal import Decimal
from typing import Any, Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.llm.gateway import LedgerWriter, LlmCallRecord
from app.llm.pricing import PriceTable
from app.llm.types import Usage
from eval.engine.batch import BATCH_DISCOUNT

DEFAULT_BUDGET_USD = Decimal("30")  # research 06 §4: whole-project default

__all__ = [
    "DEFAULT_BUDGET_USD",
    "CostSource",
    "DbCostSource",
    "MemoryCostSource",
    "SpendLedger",
    "batch_call_record",
    "budget_from_env",
    "usage_from_response_body",
    "write_batch_call",
]


def budget_from_env(env: Mapping[str, str] | None = None) -> Decimal:
    raw = (env if env is not None else os.environ).get("BUDGET_USD")
    return Decimal(raw) if raw else DEFAULT_BUDGET_USD


class CostSource(Protocol):
    async def cost_of(self, call_ids: Iterable[uuid.UUID]) -> Decimal: ...


class DbCostSource:
    """Sums `llm_calls.cost_usd` for the given ids (one short session, never held across a model call)."""

    def __init__(self, sm: async_sessionmaker[AsyncSession]) -> None:
        self._sm = sm

    async def cost_of(self, call_ids: Iterable[uuid.UUID]) -> Decimal:
        ids = list(call_ids)
        if not ids:
            return Decimal(0)
        async with self._sm() as s:
            r = await s.execute(
                text("SELECT COALESCE(SUM(cost_usd), 0) FROM llm_calls WHERE id = ANY(:ids)"), {"ids": ids}
            )
            return Decimal(r.scalar_one())


class MemoryCostSource:
    def __init__(self, costs: Mapping[uuid.UUID, Decimal] | None = None) -> None:
        self.costs: dict[uuid.UUID, Decimal] = dict(costs or {})

    async def cost_of(self, call_ids: Iterable[uuid.UUID]) -> Decimal:
        return sum((self.costs.get(i, Decimal(0)) for i in call_ids), Decimal(0))


class SpendLedger:
    def __init__(self, source: CostSource, budget_usd: Decimal | None = None) -> None:
        self.source, self.budget_usd = source, budget_usd
        self.spent = Decimal(0)
        self.call_ids: list[uuid.UUID] = []
        self._seen: set[uuid.UUID] = set()

    async def restore(self, call_ids: Iterable[uuid.UUID]) -> None:
        self.call_ids, self._seen = [], set()
        self.spent = Decimal(0)
        await self.record(call_ids)

    async def record(self, call_ids: Iterable[uuid.UUID]) -> Decimal:
        """Add the cost of calls not yet counted; returns the amount added."""
        new = [i for i in dict.fromkeys(call_ids) if i not in self._seen]
        if not new:
            return Decimal(0)
        added = await self.source.cost_of(new)
        self._seen.update(new)
        self.call_ids.extend(new)
        self.spent += added
        return added

    def exceeded(self) -> bool:
        return self.budget_usd is not None and self.spent >= self.budget_usd


# --- batch pricing ----------------------------------------------------------------------------------


def usage_from_response_body(body: Mapping[str, Any]) -> Usage:
    """Responses API usage -> Usage with the gateway's convention (uncached = total - cached - written)."""
    u = body.get("usage") or {}
    det = u.get("input_tokens_details") or {}
    cached, written = int(det.get("cached_tokens", 0) or 0), int(det.get("cache_write_tokens", 0) or 0)
    total = int(u.get("input_tokens", 0) or 0)
    return Usage(
        input_tokens=max(0, total - cached - written),
        output_tokens=int(u.get("output_tokens", 0) or 0),
        cache_read_tokens=cached,
        cache_write_tokens=written,
    )


def batch_call_record(
    *, purpose: str, model: str, usage: Usage, prices: PriceTable, run_id: uuid.UUID | None = None
) -> LlmCallRecord:
    """An `llm_calls` row for one batched response, costed at the Batch discount."""
    cost = (prices.cost(model, usage) * Decimal(str(BATCH_DISCOUNT))).quantize(Decimal("0.000001"))
    return LlmCallRecord(
        id=uuid.uuid4(),
        owner_id=None,
        run_id=run_id,
        job_id=None,
        purpose=purpose,
        lane="background",
        provider="openai",
        role=None,
        model=model,
        resolved_model=None,
        prompt_id=None,
        prompt_version=None,
        attempt=1,
        usage=usage,
        cost_usd=cost,
        latency_ms=0,
        ttft_ms=None,
        status="ok",
        error_type=None,
    )


async def write_batch_call(writer: LedgerWriter, rec: LlmCallRecord) -> uuid.UUID:
    await writer.write(rec)
    return rec.id
