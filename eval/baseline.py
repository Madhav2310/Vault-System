"""Baselines: `freeze(run)` writes `eval/baselines/<name>.json`;
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
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from eval.report import LOWER_IS_BETTER, ROOT, RunResult

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


def freeze(run: RunResult, name: str | None = None, directory: pathlib.Path = BASELINES_DIR) -> pathlib.Path:
    """Write the run's aggregate means (and per-item metrics, for later diffs) as a baseline file."""
    name = name or run.name
    data: dict[str, Any] = {
        "name": name,
        "suite": run.suite,
        "split": run.split,
        "git_sha": run.git_sha,
        "run_id": str(run.run_id),
        "n_errors": run.n_errors,
        "metrics": {m: {"mean": mean, "n": n} for m, (mean, n) in run.totals().items()},
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
