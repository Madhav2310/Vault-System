"""S17.01: paired statistics."""

import pytest
from eval.paired import bootstrap_ci, compare, mcnemar_exact, verdict
from eval.report import Sidecar, SidecarItem

import tests.support.evalkit  # noqa: F401  (puts the repo root on sys.path)


def _run(name: str, vals: dict[str, float], hits: dict[str, list[bool]] | None = None) -> Sidecar:
    items = [
        SidecarItem(
            item_id=k,
            category="lookup",
            split="train",
            metrics={"recall@8": v},
            quote_hits=(hits or {}).get(k),
        )
        for k, v in vals.items()
    ]
    return Sidecar(
        run_id=name, name=name, suite="retrieval", split="train", git_sha="x", config={}, items=items
    )


def test_bootstrap_ci() -> None:
    low, high, p = bootstrap_ci([0.1] * 40)
    assert low == pytest.approx(0.1) and high == pytest.approx(0.1) and p < 0.01
    noisy = [0.1 + (0.05 if i % 2 else -0.05) for i in range(40)]
    low, high, _ = bootstrap_ci(noisy)
    assert low < 0.1 < high and low > 0


def test_mcnemar_exact() -> None:
    assert mcnemar_exact(9, 1) == pytest.approx(0.0215, abs=0.001)
    assert mcnemar_exact(8, 2) == pytest.approx(0.1094, abs=0.001)
    assert mcnemar_exact(0, 0) == 1.0 and mcnemar_exact(5, 5) == 1.0


def test_verdicts() -> None:
    assert verdict(0.01, 0.2, 0.01) == "B better"
    assert verdict(-0.2, -0.01, 0.01) == "B worse"
    assert verdict(-0.1, 0.1, 0.5) == "no detectable difference"
    assert verdict(0.01, 0.2, 0.2) == "no detectable difference"  # CI excludes 0 but p is not small
    ids = [f"i{n}" for n in range(30)]
    a = _run("a", {i: 0.0 for i in ids}, {i: [False] for i in ids})
    b = _run("b", {i: 1.0 for i in ids}, {i: [True] for i in ids})
    c = compare(a, b, "recall@8")
    assert c.verdict == "B better" and c.p_kind.startswith("mcnemar(30/0)")
    assert compare(b, a, "recall@8").verdict == "B worse"
    assert compare(a, a, "recall@8").verdict == "no detectable difference"


def test_unpaired_excluded() -> None:
    a = _run("a", {"x": 0.5, "y": 0.5, "only_a": 1.0})
    b = _run("b", {"x": 0.6, "y": 0.7, "only_b": 0.0})
    c = compare(a, b, "recall@8")
    assert (c.n_paired, c.n_only_a, c.n_only_b) == (2, 1, 1)
    assert c.mean_a == pytest.approx(0.5) and c.mean_b == pytest.approx(0.65)
