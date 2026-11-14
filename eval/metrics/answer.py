"""Answer-quality metrics: binary judge scores, support-check rate, totals with bootstrap CIs.

Per item (added to the deterministic metrics of `eval.graders.grade_item`):
  answer_pass        completeness judge: pass -> 1, partial/fail -> 0 (deterministic verdicts count too)
  faithfulness_pass  faithfulness judge, same mapping
  unsupported_rate   share of cited segments the support check marked `unsupported` (ItemOutput.verdicts)
Judges that did not run (not applicable, error) leave the metric undefined: it is never silently 0.
"""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence

from eval.judge import Judgement
from eval.report import RunResult
from eval.schema import ItemOutput

HEADLINE = (
    "citation_integrity",
    "answer_pass",
    "faithfulness_pass",
    "cite_chunk_precision",
    "unsupported_rate",
    "abstain_precision",
    "abstain_recall",
    "latency_ms",
    "cost_usd",
)


def binary(j: Judgement | None) -> float | None:
    """pass -> 1.0; partial or fail -> 0.0; no verdict (not applicable / error / missing) -> None."""
    if j is None or j.verdict is None:
        return None
    return 1.0 if j.verdict == "pass" else 0.0


def judge_metrics(judgements: Mapping[str, Judgement]) -> dict[str, float]:
    m: dict[str, float] = {}
    for key, jt in (("answer_pass", "completeness"), ("faithfulness_pass", "faithfulness")):
        v = binary(judgements.get(jt))
        if v is not None:
            m[key] = v
    return m


def support_metrics(out: ItemOutput) -> dict[str, float]:
    if not out.verdicts:
        return {}
    return {"unsupported_rate": sum(1 for v in out.verdicts if v == "unsupported") / len(out.verdicts)}


def mean_ci(values: Sequence[float], seed: int = 7, n_boot: int = 2000) -> tuple[float, float, float]:
    """(mean, 2.5th, 97.5th percentile) of the bootstrap distribution of the mean over items."""
    if not values:
        return float("nan"), float("nan"), float("nan")
    rng = random.Random(seed)  # noqa: S311 (statistics, not security)
    m = len(values)
    means = sorted(sum(values[rng.randrange(m)] for _ in range(m)) / m for _ in range(n_boot))
    return sum(values) / m, means[int(0.025 * n_boot)], means[min(n_boot - 1, int(0.975 * n_boot))]


def totals_with_ci(run: RunResult) -> dict[str, tuple[float, float, float, int]]:
    """metric -> (mean, ci_low, ci_high, n items where defined), for every metric the run produced."""
    vals: dict[str, list[float]] = {}
    for i in run.items:
        for name, value in i.metrics.items():
            vals.setdefault(name, []).append(value)
    out: dict[str, tuple[float, float, float, int]] = {}
    for name, series in sorted(vals.items()):
        mean, lo, hi = mean_ci(series)
        out[name] = (mean, lo, hi, len(series))
    return out


def render_ci(run: RunResult) -> str:
    rows = totals_with_ci(run)
    lines = ["metric                    n    mean   [95% CI]"]
    for k in [*HEADLINE, *[m for m in rows if m not in HEADLINE]]:
        if k in rows:
            mean, lo, hi, n = rows[k]
            lines.append(f"{k:24s} {n:4d} {mean:7.3f}  [{lo:.3f}, {hi:.3f}]")
    return "\n".join(lines)
