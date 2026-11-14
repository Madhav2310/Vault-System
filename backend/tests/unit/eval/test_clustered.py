"""Clustered CIs, MDE, Clopper-Pearson."""

import math

import pytest
from eval.paired import compare
from eval.report import Sidecar, SidecarItem, clopper_pearson_upper, cluster_ci, design_effect, mde

import tests.support.evalkit  # noqa: F401  (puts the repo root on sys.path)


def test_cluster_ci() -> None:
    # 10 documents x 5 items; items within a document are identical -> strong correlation
    vals = [float(d % 2) for d in range(10) for _ in range(5)]
    docs = [f"d{d}" for d in range(10) for _ in range(5)]
    singles = [f"i{i}" for i in range(50)]
    _, lo_c, hi_c = cluster_ci(vals, docs)
    _, lo_n, hi_n = cluster_ci(vals, singles)
    assert (hi_c - lo_c) > (hi_n - lo_n) * 1.5
    assert design_effect(vals, docs) > 3
    assert cluster_ci(vals, docs) == cluster_ci(vals, docs)  # seeded
    # singleton clusters == naive: design effect 1 and width near the normal approximation
    assert design_effect(vals, singles) == pytest.approx(1.0)
    se = math.sqrt(0.25 / 50)
    assert (hi_n - lo_n) == pytest.approx(2 * 1.96 * se, rel=0.15)


def _sc(items: list[SidecarItem]) -> Sidecar:
    return Sidecar(run_id="r", name="r", suite="s", split="t", git_sha="x", config={}, items=items)


def test_mde() -> None:
    assert mde(0.04, 100) == pytest.approx(2.8 * math.sqrt(0.04 / 100), rel=0.01)
    assert mde(0.04, 100, deff=4.0) == pytest.approx(2 * mde(0.04, 100))
    a = [SidecarItem(item_id=f"i{k}", category="c", split="t", metrics={"m": 0.0}) for k in range(100)]
    b = [
        SidecarItem(
            item_id=f"i{k}", category="c", split="t", metrics={"m": 1.0 if k < 10 else 0.0}, stratum="s"
        )
        for k in range(100)
    ]
    c = compare(_sc(a), _sc(b), "m")
    var = 100 * 0.1 * 0.9 / 99  # sample variance of 10 ones in 100
    assert c.mde_80 == pytest.approx(2.8 * math.sqrt(var / 100), rel=0.01)
    assert (c.b_better, c.a_better) == (10, 0)
    assert c.strata == (("s", 100, pytest.approx(0.1)),)


def test_clopper_pearson() -> None:
    assert clopper_pearson_upper(0, 300) == pytest.approx(0.0099, abs=0.0002)
    assert clopper_pearson_upper(1, 100) == pytest.approx(0.0466, abs=0.0005)
