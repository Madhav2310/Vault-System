"""S17.12: binary judge mapping, CIs, scope filter."""

import uuid

import pytest
from eval.judge import Judgement
from eval.metrics.answer import binary, judge_metrics, mean_ci, render_ci, support_metrics
from eval.report import ItemResult, RunResult
from eval.run import select_filter
from eval.schema import ItemOutput

import tests.support.evalkit  # noqa: F401  (puts the repo root on sys.path)
from tests.support.evalkit import item


def _j(verdict: str | None, source: str = "judge") -> Judgement:
    return Judgement("i", "faithfulness", source, verdict, None, None)  # type: ignore[arg-type]


def test_ci_printed() -> None:
    assert binary(_j("pass")) == 1.0 and binary(_j("partial")) == 0.0 and binary(_j("fail")) == 0.0
    assert binary(_j(None, "error")) is None and binary(None) is None
    assert judge_metrics({"faithfulness": _j("pass"), "completeness": _j("partial")}) == {
        "faithfulness_pass": 1.0,
        "answer_pass": 0.0,
    }
    assert judge_metrics({"completeness": _j(None, "error")}) == {}
    assert support_metrics(ItemOutput(verdicts=("supported", "unsupported"))) == {"unsupported_rate": 0.5}
    mean, lo, hi = mean_ci([1.0] * 20 + [0.0] * 20)
    assert mean == 0.5 and lo < 0.5 < hi and hi - lo < 0.4
    import datetime as dt

    run = RunResult(uuid.uuid4(), "x", "full", "train", "sha", dt.datetime.now(dt.UTC))
    run.items = [ItemResult(f"i{n}", "lookup", "train", {"answer_pass": float(n % 2)}) for n in range(10)]
    out = render_ci(run)
    assert "answer_pass" in out and "[" in out and "n" in out.splitlines()[0]


def test_filter_scope_docs() -> None:
    a = item(id="a", doc_ids=["d1"])  # gold quote carries doc d1
    un = {
        "category": "unanswerable",
        "expected_coverage": "insufficient",
        "grading": "abstention",
        "gold": [],
    }
    b = item(id="b", doc_ids=["d1", "d2"], **un)
    c = item(id="c", doc_ids=[], **un)
    assert [i.id for i in select_filter([a, b, c], "scope_docs=1")] == ["a"]
    assert [i.id for i in select_filter([a, b, c], None)] == ["a", "b", "c"]
    with pytest.raises(SystemExit):
        select_filter([a], "bogus=1")
