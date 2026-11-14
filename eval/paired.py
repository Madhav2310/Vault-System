"""Paired A/B comparison of two eval runs (plan S17.01).

    cd backend && PYTHONPATH=.. uv run --frozen python -m eval.paired A.json B.json \
        --metric recall@8 [--seed 7]

Items are paired by id (items present in only one run are excluded and counted). Prints the means, the
delta (B - A) with a 95% paired-bootstrap CI over items (10,000 resamples, fixed seed), a p-value and a
verdict. For `recall@8` the p-value is the exact McNemar test on per-quote hits (discordant pairs); for other
metrics it is the two-sided bootstrap sign p-value. Verdict: `B better` when CI_low > 0 and p < 0.05,
`B worse` when CI_high < 0 and p < 0.05, else `no detectable difference`. For lower-is-better metrics
(latency, cost) read the delta in that direction yourself: the verdict text is about the raw metric value.
"""

from __future__ import annotations

import argparse
import json
import math
import pathlib
import random
import sys
from collections.abc import Sequence
from dataclasses import dataclass

from eval.report import Sidecar, cluster_ci, cluster_key, design_effect, mde

N_BOOT = 10_000


@dataclass(frozen=True)
class Comparison:
    metric: str
    n_paired: int
    n_only_a: int
    n_only_b: int
    mean_a: float
    mean_b: float
    delta: float
    ci_low: float
    ci_high: float
    p_value: float
    p_kind: str
    verdict: str
    # S21.07 additions (defaulted so existing constructions keep working)
    mean_diff: float = 0.0
    cluster_ci_low: float = 0.0
    cluster_ci_high: float = 0.0
    n_clusters: int = 0
    b_better: int = 0  # discordant items: B > A
    a_better: int = 0  # discordant items: A > B
    mde_80: float = 0.0
    design_effect: float = 1.0
    strata: tuple[tuple[str, int, float], ...] = ()  # (stratum, n, mean diff): diagnostic only


def bootstrap_ci(deltas: Sequence[float], seed: int = 7, n: int = N_BOOT) -> tuple[float, float, float]:
    """(low, high, two-sided sign p) of the mean of `deltas` over `n` bootstrap resamples of items."""
    rng = random.Random(seed)  # noqa: S311 (statistics, not security)
    m = len(deltas)
    means = sorted(sum(deltas[rng.randrange(m)] for _ in range(m)) / m for _ in range(n))
    low, high = means[int(0.025 * n)], means[min(n - 1, int(0.975 * n))]
    below = sum(1 for x in means if x <= 0) / n
    above = sum(1 for x in means if x >= 0) / n
    return low, high, min(1.0, 2 * min(below, above))


def mcnemar_exact(b01: int, b10: int) -> float:
    """Two-sided exact McNemar p on the discordant counts (A miss/B hit = b01, A hit/B miss = b10)."""
    n = b01 + b10
    if n == 0:
        return 1.0
    k = min(b01, b10)
    tail = float(sum(math.comb(n, i) for i in range(k + 1))) / 2**n
    return float(min(1.0, 2 * tail))


def verdict(ci_low: float, ci_high: float, p: float) -> str:
    if ci_low > 0 and p < 0.05:
        return "B better"
    if ci_high < 0 and p < 0.05:
        return "B worse"
    return "no detectable difference"


def compare(a: Sidecar, b: Sidecar, metric: str, seed: int = 7) -> Comparison:
    ia = {i.item_id: i for i in a.items if metric in i.metrics}
    ib = {i.item_id: i for i in b.items if metric in i.metrics}
    ids = sorted(set(ia) & set(ib))
    if not ids:
        raise SystemExit(f"no paired items define {metric!r}")
    deltas = [ib[i].metrics[metric] - ia[i].metrics[metric] for i in ids]
    low, high, p_boot = bootstrap_ci(deltas, seed)
    p, kind = p_boot, "bootstrap"
    if metric == "recall@8":
        b01 = b10 = 0
        usable = True
        for i in ids:
            ha, hb = ia[i].quote_hits, ib[i].quote_hits
            if ha is None or hb is None or len(ha) != len(hb):
                usable = False
                break
            b01 += sum(1 for x, y in zip(ha, hb, strict=True) if not x and y)
            b10 += sum(1 for x, y in zip(ha, hb, strict=True) if x and not y)
        if usable:
            p, kind = mcnemar_exact(b01, b10), f"mcnemar({b01}/{b10})"
    clusters = [
        cluster_key(i, getattr(ib[i], "source_doc", None) or getattr(ia[i], "source_doc", None)) for i in ids
    ]
    mean_d, c_lo, c_hi = cluster_ci(deltas, clusters, seed)
    deff = design_effect(deltas, clusters)
    var = sum((d - mean_d) ** 2 for d in deltas) / (len(deltas) - 1) if len(deltas) > 1 else 0.0
    by_stratum: dict[str, list[float]] = {}
    for i, d in zip(ids, deltas, strict=True):
        st = getattr(ib[i], "stratum", None) or getattr(ia[i], "stratum", None)
        if st:
            by_stratum.setdefault(st, []).append(d)
    return Comparison(
        metric,
        len(ids),
        len(set(ia) - set(ib)),
        len(set(ib) - set(ia)),
        sum(ia[i].metrics[metric] for i in ids) / len(ids),
        sum(ib[i].metrics[metric] for i in ids) / len(ids),
        sum(deltas) / len(deltas),
        low,
        high,
        p,
        kind,
        verdict(low, high, p),
        mean_d,
        c_lo,
        c_hi,
        len(set(clusters)),
        sum(1 for d in deltas if d > 0),
        sum(1 for d in deltas if d < 0),
        mde(var, len(deltas), deff),
        deff,
        tuple((st, len(v), sum(v) / len(v)) for st, v in sorted(by_stratum.items())),
    )


def render(c: Comparison) -> str:
    return (
        f"metric {c.metric}: n_paired={c.n_paired} (only A {c.n_only_a}, only B {c.n_only_b})\n"
        f"A={c.mean_a:.4f}  B={c.mean_b:.4f}  delta={c.delta:+.4f}  "
        f"95% CI [{c.ci_low:+.4f}, {c.ci_high:+.4f}]\n"
        f"clustered 95% CI [{c.cluster_ci_low:+.4f}, {c.cluster_ci_high:+.4f}] "
        f"({c.n_clusters} clusters, design effect {c.design_effect:.2f})\n"
        f"discordant: B>A {c.b_better}, A>B {c.a_better}\n"
        f"MDE at 80% power: {c.mde_80:.4f}\n"
        f"p={c.p_value:.4f} ({c.p_kind})\n"
        f"verdict: {c.verdict}"
        + "".join(f"\nstratum {st} (diagnostic): n={n} mean diff {d:+.4f}" for st, n, d in c.strata)
    )


def load(path: str) -> Sidecar:
    return Sidecar.model_validate(json.loads(pathlib.Path(path).read_text()))


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m eval.paired")
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--metric", required=True)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args(argv)
    print(render(compare(load(args.a), load(args.b), args.metric, args.seed)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
