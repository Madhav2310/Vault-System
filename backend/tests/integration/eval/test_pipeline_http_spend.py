"""S17.12 AC3: the llm_calls ids an item reports resolve, through the engine's DB cost source, to the
answering spend."""

import uuid
from decimal import Decimal

from eval.engine.ledger import DbCostSource
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.support.engine_kit import delete_calls, insert_call
from tests.support.evalkit import out


async def test_call_ids_reported(sm: async_sessionmaker[AsyncSession]) -> None:
    ids: list[uuid.UUID] = []
    try:
        await insert_call(sm, "0.013", ids)
        await insert_call(sm, "0.007", ids)
        o = out(("Virginia.", [("c1", 0, 1)]), call_ids=tuple(str(i) for i in ids))
        spend = await DbCostSource(sm).cost_of(uuid.UUID(c) for c in o.call_ids)
        assert spend == Decimal("0.020")
    finally:
        await delete_calls(sm, ids)
