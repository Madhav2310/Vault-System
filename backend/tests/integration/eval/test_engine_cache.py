"""S2.14 AC1/AC2: stage cache, keyed by (item, stage-config hash, prompt@v, model) plus the upstream chain."""

import pathlib
from decimal import Decimal

from eval.engine.cache import StageCache
from eval.engine.ledger import MemoryCostSource, SpendLedger
from eval.engine.runner import Engine, RunConfig, RunResult

from tests.support.engine_kit import SUITE, Rig, stage_values


async def _run(rig: Rig, tmp: pathlib.Path, name: str) -> RunResult:
    eng = Engine(
        rig.stages(),
        ledger=SpendLedger(MemoryCostSource(), Decimal(100)),
        cache=StageCache(tmp / "cache"),
    )
    return await eng.run(SUITE, RunConfig(name, run_id=name, runs_dir=tmp / "runs"))


async def test_full_hit(tmp_path: pathlib.Path) -> None:
    rig = Rig()
    first = await _run(rig, tmp_path, "r1")
    assert len(rig.provider.requests) == 6 and rig.retrievals == 3  # 3 items x (generation + judge)
    assert first.executed == {"retrieval": 3, "generation": 3, "judge": 3}

    again = await _run(rig, tmp_path, "r2")  # unchanged config, new run id, shared cache
    assert len(rig.provider.requests) == 6  # zero new provider calls
    assert rig.retrievals == 3
    assert not again.executed and again.cached == {"retrieval": 3, "generation": 3, "judge": 3}
    assert stage_values(again.units) == stage_values(first.units)


async def test_partial_invalidation(tmp_path: pathlib.Path) -> None:
    rig = Rig()
    await _run(rig, tmp_path, "r1")
    calls, retrievals = len(rig.provider.requests), rig.retrievals

    rig.answer_prompt = "answer_rag@2"  # only the answer prompt version changes
    r2 = await _run(rig, tmp_path, "r2")
    assert r2.executed == {"generation": 3, "judge": 3}  # downstream of the change re-runs...
    assert r2.cached == {"retrieval": 3}  # ...retrieval does not
    assert rig.retrievals == retrievals
    assert len(rig.provider.requests) == calls + 6
    assert all(v["generation"]["prompt"] == "answer_rag@2" for v in stage_values(r2.units).values())

    rig.judge_prompt = "eval_judge_faithfulness@2"  # a judge-only change re-runs only the judge
    r3 = await _run(rig, tmp_path, "r3")
    assert r3.executed == {"judge": 3} and r3.cached == {"retrieval": 3, "generation": 3}

    rig.retrieval_cfg = {"k": 12}  # a retrieval change invalidates everything after it
    r4 = await _run(rig, tmp_path, "r4")
    assert r4.executed == {"retrieval": 3, "generation": 3, "judge": 3}


async def test_model_change_misses_downstream_only(tmp_path: pathlib.Path) -> None:
    rig = Rig()
    await _run(rig, tmp_path, "r1")
    rig.model = "gpt-6.1-sol"
    r2 = await _run(rig, tmp_path, "r2")
    assert r2.executed == {"generation": 3, "judge": 3} and r2.cached == {"retrieval": 3}


async def test_corrupt_entry_is_a_miss(tmp_path: pathlib.Path) -> None:
    rig = Rig()
    await _run(rig, tmp_path, "r1")
    files = sorted((tmp_path / "cache").rglob("*.json"))
    assert files
    for f in files:
        f.write_text("{not json")
    r2 = await _run(rig, tmp_path, "r2")
    assert r2.executed == {"retrieval": 3, "generation": 3, "judge": 3}
