"""S17.12 AC1/AC5: citation integrity is computed from stored sentence counts, and the budget stop ends the
run before the next item once the spend reaches the cap."""

import pathlib

from eval.run import Harness, run_suite
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.support.evalkit import ScriptedPipeline, Tracker, chunk, item, out

C1 = chunk("c1", "d1", "The Agreement is governed by the laws of Virginia. Payment is due in 30 days.")


async def test_citation_integrity(
    sm: async_sessionmaker[AsyncSession], tracker: Tracker, tmp_path: pathlib.Path
) -> None:
    items = [item(id="toy:a"), item(id="toy:b")]
    outputs = {
        "toy:a": out(("Virginia.", [("c1", 0, 1)]), retrieved=["c1"]),
        "toy:b": out(("Net 30.", [("c1", 1, 2)]), retrieved=["c1"]),
    }
    run, _ = await run_suite(
        items, Harness(ScriptedPipeline(outputs), {"d1": [C1]}), name="toy", suite="full", split="all",
        sm=sm, report_dir=tmp_path, git_sha="abc1234",
    )  # fmt: skip
    tracker.run_ids.append(run.run_id)
    mean, n = run.totals()["citation_integrity"]
    assert (mean, n) == (1.0, 2)


async def test_budget_stop(
    sm: async_sessionmaker[AsyncSession], tracker: Tracker, tmp_path: pathlib.Path
) -> None:
    items = [item(id=f"toy:{i}") for i in range(5)]
    outputs = {f"toy:{i}": out(("Virginia.", [("c1", 0, 1)]), retrieved=["c1"]) for i in range(5)}
    pipe = ScriptedPipeline(outputs)
    spent = 0.0

    async def paid() -> float:
        nonlocal spent
        spent = 0.02 * len(pipe.calls)  # each answered item costs $0.02
        return spent

    run, _ = await run_suite(
        items, Harness(pipe, {"d1": [C1]}, spent=paid), name="toy", suite="full", split="all",
        sm=sm, report_dir=tmp_path, git_sha="abc1234", max_usd=0.05,
    )  # fmt: skip
    tracker.run_ids.append(run.run_id)
    assert run.config["stopped"] == "budget"
    assert len(run.items) == 3  # stops once spend (0.06) >= cap (0.05): overshoot is under one item's cost
    assert spent - 0.05 < 0.02 + 1e-9
