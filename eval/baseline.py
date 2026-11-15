"""Baselines: `freeze(run, name)` writes `eval/baselines/<name>.json`;
`compare_to_baseline(run, baseline, tolerance)` reports every metric that got worse beyond tolerance.

Higher-is-better metrics regress when `baseline_mean - mean > tolerance` (absolute). Metrics in
`report.LOWER_IS_BETTER` regress when `mean > baseline_mean * (1 + tolerance)`. A metric the baseline has and
the run lacks is a regression (a grader silently disappearing must not read as green). A run over a different
item set than the baseline is not comparable and raises `ValueError`. More errored items than the baseline is
a regression too.
"""

from __future__ import annotations

import json
import math
import pathlib
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from eval.attribution import STAGES, UNKNOWN, from_sidecar_item, stage_table
from eval.report import LOWER_IS_BETTER, ROOT, RunResult, cluster_ci, cluster_key, design_effect, mde, sidecar

BASELINES_DIR = ROOT / "eval" / "baselines"
DEFAULT_TOLERANCE = 0.02
_EPS = 1e-9


class BaselineRegression(Exception):
    def __init__(self, regressions: list[Regression]) -> None:
        super().__init__("; ".join(str(r) for r in regressions))
        self.regressions = regressions


@dataclass(frozen=True)
class Regression:
    metric: str
    baseline: float
    current: float | None
    tolerance: float

    def __str__(self) -> str:
        cur = "missing" if self.current is None else f"{self.current:.4f}"
        return f"{self.metric}: {self.baseline:.4f} -> {cur} (tolerance {self.tolerance})"


@dataclass
class Comparison:
    regressions: list[Regression] = field(default_factory=list)
    deltas: dict[str, float] = field(
        default_factory=dict
    )  # metric -> signed gain (negative = worse, in tolerance)

    @property
    def ok(self) -> bool:
        return not self.regressions

    def raise_on_regression(self) -> None:
        if self.regressions:
            raise BaselineRegression(self.regressions)


def _metric_series(run: RunResult, metric: str, stratum: str | None = None) -> tuple[list[float], list[str]]:
    vals: list[float] = []
    clus: list[str] = []
    for i in run.items:
        if stratum is not None and i.stratum != stratum:
            continue
        if metric in i.metrics:
            vals.append(i.metrics[metric])
            clus.append(cluster_key(i.item_id, i.source_doc))
    return vals, clus


def freeze(run: RunResult, name: str, directory: pathlib.Path = BASELINES_DIR) -> pathlib.Path:
    """`eval/baselines/<name>.json`: means with clustered CIs, per-stratum table (diagnostic), MDE inputs,
    attribution counts per stage, and the per-item metrics for later diffs."""
    metrics: dict[str, Any] = {}
    mde_in: dict[str, Any] = {}
    for m in run.metric_names():
        v, c = _metric_series(run, m)
        mean, lo, hi = cluster_ci(v, c)
        var = sum((x - mean) ** 2 for x in v) / len(v)
        deff = design_effect(v, c)
        metrics[m] = {"mean": mean, "n": len(v), "ci_low": lo, "ci_high": hi, "clusters": len(set(c))}
        # inputs for the paired MDE (eval.paired recomputes it from discordant pairs); the unpaired figure is
        # the two-independent-runs worst case: var_diff = 2 * var
        mde_in[m] = {
            "n": len(v),
            "var": var,
            "design_effect": deff,
            "clusters": len(set(c)),
            "mde_80_unpaired": mde(2 * var, len(v), deff),
        }
    strata = sorted({i.stratum for i in run.items if i.stratum})
    per_stratum = {
        s: {
            "n": sum(1 for i in run.items if i.stratum == s),
            "note": "diagnostic",
            "metrics": {
                m: (sum(v) / len(v)) for m in run.metric_names() if (v := _metric_series(run, m, s)[0])
            },
        }
        for s in strata
    }
    inputs = [
        from_sidecar_item(it, raw.attribution) for it, raw in zip(sidecar(run).items, run.items, strict=True)
    ]
    table = stage_table(inputs)
    failed = [a for a in inputs if a.failed]
    not_recorded = Counter(
        k
        for i in run.items
        if i.attribution and i.attribution.get("failed")
        for k in i.attribution
        if k != "failed" and i.attribution.get(k) is None
    )
    attribution = {
        "failed": len(failed),
        "by_stage": {
            s: {"count": c, "share": sh, "ci_low": lo, "ci_high": hi} for s, (c, sh, lo, hi) in table.items()
        },
        "stages": [*STAGES, UNKNOWN],
        "not_recorded_among_failed": dict(sorted(not_recorded.items())),
    }
    data: dict[str, Any] = {
        "name": name,
        "dataset": run.config.get("dataset"),
        "suite": run.suite,
        "split": run.split,
        "git_sha": run.git_sha,
        "run_id": str(run.run_id),
        "n_items": len(run.items),
        "n_errors": run.n_errors,
        "spent_usd": run.config.get("spent_usd"),
        "clustered_by": "source_doc",
        "metrics": metrics,
        "per_stratum": per_stratum,
        "mde_inputs": mde_in,
        "attribution": attribution,
        "per_item": {i.item_id: i.metrics for i in run.items},
    }
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.json"
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    return path


def load_baseline(path: pathlib.Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text())
    return data


def compare_to_baseline(
    run: RunResult,
    baseline: Mapping[str, Any] | pathlib.Path,
    tolerance: float | Mapping[str, float] = DEFAULT_TOLERANCE,
) -> Comparison:
    base = load_baseline(baseline) if isinstance(baseline, pathlib.Path) else baseline
    if set(base["per_item"]) != {i.item_id for i in run.items}:
        raise ValueError("run and baseline cover different item sets; means are not comparable")
    cur = run.totals()
    out = Comparison()

    def tol(metric: str) -> float:
        return tolerance if isinstance(tolerance, float | int) else tolerance.get(metric, DEFAULT_TOLERANCE)

    for metric, b in sorted(base["metrics"].items()):
        bm, t = float(b["mean"]), float(tol(metric))
        if metric not in cur or math.isnan(cur[metric][0]):
            out.regressions.append(Regression(metric, bm, None, t))
            continue
        cm = cur[metric][0]
        worse = cm > bm * (1 + t) + _EPS if metric in LOWER_IS_BETTER else bm - cm > t + _EPS
        if worse:
            out.regressions.append(Regression(metric, bm, cm, t))
        elif cm != bm:
            out.deltas[metric] = (bm - cm) if metric in LOWER_IS_BETTER else (cm - bm)
    if run.n_errors > int(base.get("n_errors", 0)):
        out.regressions.append(
            Regression("errored_items", float(base.get("n_errors", 0)), float(run.n_errors), 0.0)
        )
    return out
