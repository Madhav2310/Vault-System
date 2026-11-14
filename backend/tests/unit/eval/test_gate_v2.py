"""Each gate criterion on synthetic sidecars (no model call, no DB)."""

import json
import pathlib
import random
from typing import Any

import pytest
from eval import gate
from eval.report import Sidecar, SidecarItem

N = 100


def make(metrics: dict[str, list[float]], name: str = "run", errors: int = 0, docs: int = 25) -> Sidecar:
    n = len(next(iter(metrics.values())))
    return Sidecar(
        run_id=name, name=name, suite="full", split="dev", git_sha="abc1234", config={},
        items=[
            SidecarItem(
                item_id=f"i{k:03d}", category="v2", split="dev",
                metrics={m: v[k] for m, v in metrics.items()},
                source_doc=f"doc{k % docs}", error="boom" if k < errors else None,
            )
            for k in range(n)
        ],
    )  # fmt: skip


def bits(n_ones: int, n: int = N, seed: int = 1) -> list[float]:
    v = [1.0] * n_ones + [0.0] * (n - n_ones)
    random.Random(seed).shuffle(v)
    return v


def write(tmp: pathlib.Path, sc: Sidecar) -> str:
    p = tmp / f"{sc.name}.json"
    p.write_text(sc.model_dump_json())
    return str(p)


def base_metrics() -> dict[str, list[float]]:
    return {
        "answer_pass": bits(70), "support_rate": bits(80, seed=2), "abstention_correct": bits(90, seed=3),
        "citation_integrity": [1.0] * N, "injection_violation": [0.0] * N,
    }  # fmt: skip


def status(checks: list[gate.Check], name: str) -> str:
    return next(c.status for c in checks if c.name == name)


def test_answer_pass_drop_fails() -> None:
    base = make(base_metrics())
    m = base_metrics()
    drop = [k for k, v in enumerate(m["answer_pass"]) if v == 1.0][:6]
    for k in drop:
        m["answer_pass"][k] = 0.0
    c = gate.noninferiority(base, make(m), "answer_pass", gate.MARGIN, 7)
    assert c.status == gate.FAIL and "-0.0600" in c.detail


def test_identical_passes_with_power() -> None:
    base = make(base_metrics())
    c = gate.noninferiority(base, make(base_metrics()), "answer_pass", gate.MARGIN, 7)
    assert c.status == gate.PASS and "MDE 0.0000" in c.detail


def test_insufficient_power_is_inconclusive_not_pass() -> None:
    n = 400
    ap = bits(280, n)
    cand = list(ap)
    ones = [k for k, v in enumerate(ap) if v == 1.0]
    zeros = [k for k, v in enumerate(ap) if v == 0.0]
    for k in ones[:6]:  # 6 losses and 6 wins: delta 0, CI clears -2 points, but MDE ~ 2.4 points > margin
        cand[k] = 0.0
    for k in zeros[:6]:
        cand[k] = 1.0
    c = gate.noninferiority(
        make({"answer_pass": ap}, docs=100),
        make({"answer_pass": cand}, docs=100),
        "answer_pass",
        gate.MARGIN,
        7,
    )
    assert c.status == gate.INCONCLUSIVE
    assert "too little power" in c.detail


def test_wider_margin_flips_fail_to_pass() -> None:
    base = make(base_metrics())
    m = base_metrics()
    for k in [k for k, v in enumerate(m["answer_pass"]) if v == 1.0][:3]:
        m["answer_pass"][k] = 0.0
    assert gate.noninferiority(base, make(m), "answer_pass", 0.2, 7).status != gate.FAIL


def test_missing_metric_fails() -> None:
    m = base_metrics()
    del m["support_rate"]
    assert (
        gate.noninferiority(make(base_metrics()), make(m), "support_rate", gate.MARGIN, 7).status == gate.FAIL
    )


def test_injection_violation_fails() -> None:
    m = base_metrics()
    m["injection_violation"][4] = 1.0
    checks = gate.must_not_regress(make(base_metrics()), make(m))
    assert status(checks, "injection_violation") == gate.FAIL
    assert (
        status(gate.must_not_regress(make(base_metrics()), make(base_metrics())), "injection_violation")
        == gate.PASS
    )


def test_abstention_drop_and_citation_integrity() -> None:
    m = base_metrics()
    k = m["abstention_correct"].index(1.0)
    m["abstention_correct"][k] = 0.0
    m["citation_integrity"][0] = 0.5
    checks = gate.must_not_regress(make(base_metrics()), make(m))
    assert status(checks, "abstention_correct") == gate.FAIL
    assert status(checks, "citation_integrity") == gate.FAIL
    better = base_metrics()
    better["abstention_correct"][better["abstention_correct"].index(0.0)] = 1.0
    assert (
        status(gate.must_not_regress(make(base_metrics()), make(better)), "abstention_correct") == gate.PASS
    )


def test_coverage_and_errors() -> None:
    base, cand = make(base_metrics()), make(base_metrics(), errors=2)
    checks = gate.coverage_checks(base, cand, 0, 2)
    assert status(checks, "errors") == gate.FAIL and status(checks, "coverage") == gate.PASS
    subset = Sidecar(**{**cand.model_dump(), "items": cand.items[:60]})
    assert status(gate.coverage_checks(base, subset, 0, 0), "coverage") == gate.PASS  # a subset is fine
    foreign = Sidecar(**{**base.model_dump(), "items": [base.items[0].model_copy(update={"item_id": "zzz"})]})
    assert status(gate.coverage_checks(base, foreign, 0, 0), "coverage") == gate.FAIL


def test_load_frozen_baseline_form(tmp_path: pathlib.Path) -> None:
    data: dict[str, Any] = {"name": "answer_v2", "n_errors": 1, "per_item": {"a": {"answer_pass": 1.0}}}
    p = tmp_path / "b.json"
    p.write_text(json.dumps(data))
    sc, errs = gate.load_any(p)
    assert errs == 1 and sc.items[0].item_id == "a"


def test_citations_subcommand(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    good = write(tmp_path, make({"citation_integrity": [1.0, 1.0]}, "good"))
    bad = write(tmp_path, make({"citation_integrity": [1.0, 0.5]}, "bad"))
    assert gate.main(["citations", good]) == 0
    assert gate.main(["citations", bad]) == 1
    assert "broken: i001" in capsys.readouterr().out


def test_strict_power_fails_inconclusive(tmp_path: pathlib.Path) -> None:
    base_sc = make(base_metrics())
    m = base_metrics()
    ap = m["answer_pass"]
    for k in [k for k, v in enumerate(ap) if v == 1.0][:7]:
        ap[k] = 0.0
    for k in [k for k, v in enumerate(ap) if v == 0.0][:14][7:]:
        ap[k] = 1.0
    b, c = write(tmp_path, base_sc), write(tmp_path, make(m, "cand"))
    # CI may or may not clear the margin; strict mode must never be more lenient than the default
    assert gate.main(["compare", b, c, "--strict-power"]) >= gate.main(["compare", b, c])
