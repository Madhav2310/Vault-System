"""S2.14 AC4: the hard spend ceiling, computed from llm_calls, stops the run BEFORE the next call."""

import json
import pathlib
import uuid
from decimal import Decimal

from eval.engine.ledger import DbCostSource, MemoryCostSource, SpendLedger, budget_from_env
from eval.engine.runner import Engine, Item, RunConfig, Suite
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.support.engine_kit import Rig, delete_calls, insert_call

SUITE5 = Suite("five", [Item(f"q{i}", {"i": i}) for i in range(5)])


async def test_budget_stop(sm: async_sessionmaker[AsyncSession], tmp_path: pathlib.Path) -> None:
    ids: list[uuid.UUID] = []
    try:
        rig = Rig()

        async def paid_call() -> uuid.UUID:
            return await insert_call(sm, "0.02", ids)  # each generation costs $0.02 in llm_calls

        rig.make_call_id = paid_call
        res = await Engine(rig.stages(), ledger=SpendLedger(DbCostSource(sm), Decimal("0.05"))).run(
            SUITE5, RunConfig("b", run_id="budget", runs_dir=tmp_path)
        )
        # generations cost .02, .04, .06 >= .05: the judge of unit 3 (the next call) never runs
        assert res.stopped == "budget" and res.status == "stopped"
        assert rig.gen_calls == 3
        assert len(rig.provider.requests) == 3 + 2  # 3 generations + the judges of units 1 and 2 only
        assert res.spent_usd == Decimal("0.06")
        st = json.loads((tmp_path / "budget" / "_state.json").read_text())
        assert st["stopped"] == "budget" and st["status"] == "stopped" and st["spent_usd"] == 0.06
        assert st["budget_usd"] == 0.05 and len(st["call_ids"]) == 3
        assert st["completed"] == ["q0#r0", "q1#r0"]

        # raising the ceiling resumes the same run; spend is recomputed from llm_calls
        res2 = await Engine(rig.stages(), ledger=SpendLedger(DbCostSource(sm), Decimal("1"))).run(
            SUITE5, RunConfig("b", run_id="budget", runs_dir=tmp_path)
        )
        assert res2.status == "completed" and res2.stopped is None and res2.resumed_units == 2
        assert rig.gen_calls == 3 + 3  # q2 (no stage cache here) and q3, q4; q0/q1 are not repeated
        assert res2.spent_usd == Decimal("0.02") * len(ids)
    finally:
        await delete_calls(sm, ids)


async def test_no_budget_means_no_ceiling_and_env_default(tmp_path: pathlib.Path) -> None:
    res = await Engine(Rig().stages(), ledger=SpendLedger(MemoryCostSource(), None)).run(
        SUITE5, RunConfig("n", run_id="nb", runs_dir=tmp_path)
    )
    assert res.status == "completed"
    assert budget_from_env({}) == Decimal("30") and budget_from_env({"BUDGET_USD": "12.5"}) == Decimal("12.5")
