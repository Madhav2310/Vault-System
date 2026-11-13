"""S2.14 AC5: a killed run resumes from _state.json without repeating completed items; atomic writes."""

import json
import pathlib

import pytest
from eval.engine.ledger import MemoryCostSource, SpendLedger
from eval.engine.runner import Engine, Item, RunConfig, StageCtx, StageOutput, Suite
from eval.engine.state import RunDir, atomic_write_text

from tests.support.engine_kit import Rig

SUITE5 = Suite("five", [Item(f"q{i}", {"i": i}) for i in range(5)])


def _engine(rig: Rig) -> Engine:
    return Engine(
        rig.stages(), ledger=SpendLedger(MemoryCostSource())
    )  # no stage cache: resume is state-only


async def test_resume(tmp_path: pathlib.Path) -> None:
    killed = Rig(kill_after=3)  # the third generation call dies with KeyboardInterrupt
    cfg = RunConfig("r", run_id="resume", runs_dir=tmp_path)
    with pytest.raises(KeyboardInterrupt):
        await _engine(killed).run(SUITE5, cfg)
    rd = RunDir(tmp_path / "resume")
    st = rd.load_state()
    assert st is not None and st.completed == ["q0#r0", "q1#r0"] and st.status == "interrupted"
    with rd.results_path.open("a") as f:
        f.write('{"unit": "q2#r0", "item": "q2", "rep": 0, "stag')  # torn last line from a hard kill

    fresh = Rig()
    res = await _engine(fresh).run(SUITE5, cfg)
    assert res.status == "completed" and res.resumed_units == 2
    assert fresh.gen_calls == 3 and fresh.retrievals == 3  # only q2..q4; q0 and q1 were not repeated
    assert len(fresh.provider.requests) == 6
    assert sorted(res.units) == [f"q{i}#r0" for i in range(5)]
    lines = rd.results_path.read_text().splitlines()
    assert len(lines) == 5 and all(json.loads(ln)["error"] is None for ln in lines)  # torn line repaired

    again = Rig()  # a finished run is a no-op
    res3 = await _engine(again).run(SUITE5, cfg)
    assert again.gen_calls == 0 and res3.resumed_units == 5 and res3.status == "completed"


async def test_errored_unit_is_retried_on_resume(tmp_path: pathlib.Path) -> None:
    rig = Rig()
    real = rig._generation
    boom = {"on": True}

    async def flaky(ctx: StageCtx) -> StageOutput:
        if boom["on"] and ctx.item.id == "q1":
            raise RuntimeError("provider 500")
        return await real(ctx)

    rig._generation = flaky  # type: ignore[method-assign]
    cfg = RunConfig("r", run_id="flaky", runs_dir=tmp_path)
    r1 = await _engine(rig).run(SUITE5, cfg)
    assert list(r1.errors) == ["q1#r0"] and "RuntimeError" in r1.errors["q1#r0"]
    assert r1.status == "completed"  # errors are recorded, not fatal
    boom["on"] = False
    r2 = await _engine(rig).run(SUITE5, cfg)
    assert r2.errors == {} and r2.resumed_units == 4  # only q1 re-ran; its error line is superseded


async def test_config_mismatch_refuses_resume(tmp_path: pathlib.Path) -> None:
    cfg = RunConfig("r", run_id="x", runs_dir=tmp_path)
    await _engine(Rig()).run(SUITE5, cfg)
    with pytest.raises(ValueError, match="different suite/stage config"):
        await _engine(Rig(answer_prompt="answer_rag@2")).run(SUITE5, cfg)


def test_atomic_write_and_torn_line(tmp_path: pathlib.Path) -> None:
    p = tmp_path / "a" / "f.json"
    atomic_write_text(p, "one")
    atomic_write_text(p, "two")
    assert p.read_text() == "two" and [x.name for x in p.parent.iterdir()] == ["f.json"]  # no temp left
    rd = RunDir(tmp_path / "run")
    rd.append_result({"unit": "a#r0", "v": 1})
    rd.append_result({"unit": "b#r0", "v": 2})
    rd.results_path.write_text(rd.results_path.read_text() + '{"unit": "c#r0", "v')
    assert sorted(rd.read_results()) == ["a#r0", "b#r0"]  # the reader tolerates the torn tail
    assert rd.repair_results() > 0 and rd.results_path.read_text().endswith("\n")
    rd.append_result({"unit": "c#r0", "v": 3})
    assert sorted(rd.read_results()) == ["a#r0", "b#r0", "c#r0"]
    rd.results_path.write_text('garbage\n{"unit": "a#r0"}\n')
    with pytest.raises(ValueError, match="line 1"):
        rd.read_results()  # corruption in the middle is not silently skipped


def test_change_md_per_round(tmp_path: pathlib.Path) -> None:
    p = RunDir(tmp_path).write_change(3, "# Round 3\nchanged answer prompt")
    assert p == tmp_path / "rounds" / "03" / "change.md" and p.read_text().endswith("prompt\n")
