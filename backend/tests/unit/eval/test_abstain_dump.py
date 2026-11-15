"""A v2 answers run writes `<run_id>.abstain.jsonl` with exactly the fields `eval.calibrate_abstain`
reads; the loader round-trips it. Scripted pipeline, FakeProvider judges, $0."""

import json
import pathlib
from typing import Any

from eval.answers_dump import abstain_path, abstain_row
from eval.calibrate_abstain import load_rows
from eval.run import Harness
from eval.run_v2 import ItemOutputV2, run_suite_v2
from eval.schema import AnswerSegment, CitedRange, EvalContext
from eval.splits_v2 import split_of

from app.chat import abstain as A
from app.core.settings import Settings
from app.llm.gateway import LlmGateway, MemoryLedger
from app.llm.pricing import DEFAULT_PRICES, PriceTable
from app.llm.prompts import default_registry
from tests.support.evalkit import ScriptedPipeline
from tests.support.llm import SleepRecorder
from tests.unit.eval.test_answers_dump import C1, UNITS, Judges, v2

FIELDS = {"item_id", "stratum", "source_doc", "top", "margin", "unsupported"}


def cands(*scores: float) -> list[dict[str, Any]]:
    return [{"rerank_score": s, "vector_distance": None, "id_match": False} for s in scores]


def test_abstain_row_uses_the_gate_features() -> None:
    row = abstain_row(
        v2(stratum="unanswerable", answer_type="abstain", required_claims=[], gold_evidence=[]),
        {"candidates": cands(0.2, 0.9, 0.5, 0.1), "run": {"unsupported": 0.5}},
    )
    assert set(row) == FIELDS
    # best 0.9; rank-3 score is 0.2 (sorted 0.9, 0.5, 0.2, 0.1): margin 0.7, same function as the gate
    assert row["top"] == 0.9 and abs(row["margin"] - 0.7) < 1e-12
    assert row["unsupported"] == 0.5
    assert row["stratum"] == "unanswerable" and row["source_doc"] == "d1"


def test_abstain_row_leaves_unrecorded_features_null() -> None:
    row = abstain_row(v2(), {"candidates": cands(0.8, 0.4), "run": None})
    assert row["top"] == 0.8 and row["margin"] is None  # fewer than 3 scored candidates
    assert row["unsupported"] is None
    empty = abstain_row(v2(), {"candidates": [], "run": {}})
    assert empty["top"] is None and empty["margin"] is None


async def test_run_writes_file_the_calibrator_loads(tmp_path: pathlib.Path) -> None:
    s = Settings(_env_file=None, database_url="postgresql+asyncpg://x/y", redis_url="redis://x")  # type: ignore[call-arg]
    fake = Judges()
    gw = LlmGateway(
        chat=fake,
        embeddings=fake,
        prompts=default_registry(),
        prices=PriceTable({**DEFAULT_PRICES}),
        ledger=MemoryLedger(),
        settings=s,
        sleep=SleepRecorder(),
        rng=lambda: 0.5,
    )
    items = [v2(), v2(id="u1", stratum="unanswerable", answer_type="abstain", required_claims=[],
                      gold_evidence=[])]  # fmt: skip
    outs: Any = {
        "a1": ItemOutputV2(
            retrieved=["c1"],
            segments=[AnswerSegment("Virginia law governs.", (CitedRange("c1", 0, 1),))],
            abstain_inputs={
                "candidates": cands(0.9, 0.6, 0.3),
                "run": {"unsupported": 0.0},
            },
        ),
        "u1": ItemOutputV2(
            retrieved=["c1"],
            segments=[AnswerSegment("Not stated.")],
            abstained=True,
            abstain_inputs={"candidates": cands(0.1), "run": None},
        ),
    }
    h = Harness(ScriptedPipeline(outs), {"d1": [C1]}, EvalContext(), judge_gw=gw, gold_units=lambda d: UNITS)
    run, _ = await run_suite_v2(
        items, h, name="toy", suite="full", split="all", report_dir=tmp_path, git_sha="abc1234"
    )
    path = abstain_path(run.run_id, tmp_path)
    assert path.exists()
    lines = [json.loads(ln) for ln in path.read_text().splitlines()]
    assert [set(d) for d in lines] == [FIELDS, FIELDS]
    assert [d["item_id"] for d in lines] == ["a1", "u1"]

    rows = load_rows(path)  # the calibrator's own loader
    assert [r.item_id for r in rows] == ["a1", "u1"]
    assert rows[0].split == split_of("d1")
    assert rows[0].x["top"] == 0.9 and rows[0].x["unsupported"] == 0.0
    assert abs(rows[0].x["margin"] - 0.6) < 1e-12  # type: ignore[operator]
    assert rows[1].should_abstain and rows[1].x["margin"] is None and rows[1].x["unsupported"] is None
    assert set(rows[0].x) == set(A.FEATURES)


async def test_no_inputs_no_file(tmp_path: pathlib.Path) -> None:
    """A pipeline that records nothing (the in-process one) writes no abstain file."""
    h = Harness(
        ScriptedPipeline({"a1": ItemOutputV2(retrieved=["c1"])}),
        {"d1": [C1]},
        EvalContext(),
        gold_units=lambda d: UNITS,
    )
    run, _ = await run_suite_v2([v2()], h, name="toy", suite="retrieval", split="all", report_dir=tmp_path)
    assert not abstain_path(run.run_id, tmp_path).exists()
