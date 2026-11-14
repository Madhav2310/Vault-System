"""Stage attribution."""

import json
import pathlib
from typing import Any

import pytest
from eval.attribution import (
    STAGES,
    UNKNOWN,
    AttributionInput,
    attribute,
    from_sidecar_item,
    main,
    stage_table,
)
from eval.report import SidecarItem

import tests.support.evalkit  # noqa: F401  (puts the repo root on sys.path)

# One minimal failure per stage; every earlier check is explicitly clean.
_CLEAN = {"gold_in_parsed": True, "gold_in_chunk": True, "gold_in_candidates": True, "gold_in_evidence": True}
FIXTURE: dict[str, dict[str, Any]] = {
    "extraction": {**_CLEAN, "gold_in_parsed": False},
    "chunking": {**_CLEAN, "gold_in_chunk": False},
    "retrieval": {**_CLEAN, "gold_in_candidates": False},
    "ranking": {**_CLEAN, "gold_in_evidence": False},
    "utilization": {**_CLEAN, "gold_cited": False},
    "citation": {**_CLEAN, "gold_cited": True, "cited_outside_gold": True},
    "generation": {**_CLEAN, "gold_cited": True, "claim_missing_or_unsupported": True},
    "abstention": {"should_abstain": True, "abstained": False},
    "grading": {**_CLEAN, "gold_cited": True, "human_label": "pass", "judge_verdict": "fail"},
}


def _a(item_id: str, failed: bool = True, **kw: Any) -> AttributionInput:
    return AttributionInput(item_id, failed, **kw)


def test_each_stage() -> None:
    assert set(FIXTURE) == set(STAGES)
    for stage, kw in FIXTURE.items():
        assert attribute(_a(stage, **kw)) == stage
    table = stage_table([_a(s, **kw) for s, kw in FIXTURE.items()])
    assert all(table[s][0] == 1 for s in STAGES) and table[UNKNOWN][0] == 0


def test_earliest_stage_wins() -> None:
    both = {**FIXTURE["retrieval"], "cited_outside_gold": True, "claim_missing_or_unsupported": True}
    assert attribute(_a("x", **both)) == "retrieval"
    assert attribute(_a("y", gold_in_candidates=False, gold_in_parsed=False)) == "extraction"


def test_passing_unattributed_and_unknown() -> None:
    assert attribute(_a("p", failed=False, gold_in_parsed=False)) is None
    assert attribute(_a("u")) == UNKNOWN  # nothing recorded: no crash, no guess
    assert attribute(_a("v", gold_in_parsed=True)) == UNKNOWN
    assert attribute(_a("w", unresolved_citations=2, gold_in_evidence=True, gold_cited=True)) == "citation"


def _write(path: pathlib.Path, items: list[dict[str, Any]]) -> str:
    full = [{"category": "c", "split": "t", "metrics": {}, **it} for it in items]
    doc = {
        "run_id": "r",
        "name": "n",
        "suite": "s",
        "split": "t",
        "git_sha": "x",
        "config": {},
        "items": full,
    }
    path.write_text(json.dumps(doc))
    return str(path)


def test_adapter_from_current_sidecar() -> None:
    m = {"recall@8": 0.0, "recall@40": 0.0, "answer_pass": 0.0}
    a = from_sidecar_item(SidecarItem(item_id="i", category="c", split="t", metrics=m))
    assert a.failed and attribute(a) == "retrieval"
    ok = SidecarItem(item_id="j", category="c", split="t", metrics={"answer_pass": 1.0})
    assert not from_sidecar_item(ok).failed


def test_cli(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    a = _write(
        tmp_path / "a.json",
        [
            {
                "item_id": "1",
                "metrics": {"answer_pass": 0.0},
                "source_doc": "d1",
                "attribution": FIXTURE["extraction"],
            },
            {"item_id": "2", "metrics": {"answer_pass": 0.0}, "source_doc": "d1"},
            {"item_id": "3", "metrics": {"answer_pass": 1.0}},
        ],
    )
    b = _write(tmp_path / "b.json", [{"item_id": "1", "metrics": {"answer_pass": 1.0}}])
    assert main([a]) == 0
    out = capsys.readouterr().out
    assert "| extraction | 1 |" in out and "| unknown_stage | 1 |" in out and "failed: 2" in out
    assert main([a, "--vs", b]) == 0
    assert "B-A count" in capsys.readouterr().out
