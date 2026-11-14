"""Run results and the Markdown report (`eval/reports/<run_id>.md`): header, totals, per-category means and a
per-item table. Totals average each metric only over the items where it is defined (n is shown)."""

from __future__ import annotations

import datetime as dt
import math
import pathlib
import random
import uuid
from collections import defaultdict
from collections.abc import Hashable, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

ROOT = pathlib.Path(__file__).resolve().parents[1]
REPORTS_DIR = ROOT / "eval" / "reports"
# Metrics where lower is better; everything else is higher-is-better.
LOWER_IS_BETTER = frozenset({"latency_ms", "ttft_ms", "cost_usd", "gold_unmapped"})


@dataclass
class ItemResult:
    item_id: str
    category: str
    split: str
    metrics: dict[str, float]
    error: str | None = None
    turns: list[ItemResult] = field(default_factory=list)  # multi-turn: one per turn, ids `<id>#t<n>`
    quote_hits: list[bool] | None = None  # per mapped gold quote: hit in the top 8 (paired tests)
    degraded: tuple[str, ...] = ()
    source_doc: str | None = None  # cluster key (S21.07); None = singleton cluster
    stratum: str | None = None
    attribution: dict[str, Any] | None = None  # S21.08 signals the runner recorded (None = not recorded)


@dataclass
class RunResult:
    run_id: uuid.UUID
    name: str
    suite: str
    split: str
    git_sha: str
    started_at: dt.datetime
    finished_at: dt.datetime | None = None
    items: list[ItemResult] = field(default_factory=list)
    config: dict[str, Any] = field(default_factory=dict)

    @property
    def n_errors(self) -> int:
        return sum(1 for i in self.items if i.error or any(t.error for t in i.turns))

    def degraded_rate(self) -> float:
        """Share of items whose result was degraded (no_rerank, lexical_only...)."""
        return sum(1 for i in self.items if i.degraded) / len(self.items) if self.items else 0.0

    def degraded_by_kind(self) -> dict[str, int]:
        out: dict[str, int] = defaultdict(int)
        for i in self.items:
            for k in set(i.degraded):
                out[k] += 1
        return dict(sorted(out.items()))

    def metric_names(self) -> list[str]:
        return sorted({m for i in self.items for m in i.metrics})

    def totals(self, category: str | None = None) -> dict[str, tuple[float, int]]:
        """metric -> (mean over items where defined, n items)."""
        vals: dict[str, list[float]] = defaultdict(list)
        for i in self.items:
            if category is None or i.category == category:
                for k, v in i.metrics.items():
                    vals[k].append(v)
        return {k: (sum(v) / len(v), len(v)) for k, v in sorted(vals.items())}


class SidecarItem(BaseModel):
    item_id: str
    category: str
    split: str
    metrics: dict[str, float]
    quote_hits: list[bool] | None = None
    error: str | None = None
    source_doc: str | None = None
    stratum: str | None = None
    attribution: dict[str, Any] | None = None


class Sidecar(BaseModel):
    """`eval/reports/<run_id>.json`: machine-readable twin of the Markdown report (input of `eval.paired`)."""

    run_id: str
    name: str
    suite: str
    split: str
    git_sha: str
    config: dict[str, Any]
    items: list[SidecarItem]


def sidecar(run: RunResult) -> Sidecar:
    return Sidecar(
        run_id=str(run.run_id),
        name=run.name,
        suite=run.suite,
        split=run.split,
        git_sha=run.git_sha,
        config=run.config,
        items=[
            SidecarItem(
                item_id=i.item_id,
                category=i.category,
                split=i.split,
                metrics=i.metrics,
                quote_hits=i.quote_hits,
                error=i.error,
                source_doc=i.source_doc,
                stratum=i.stratum,
                attribution=i.attribution,
            )
            for i in run.items
        ],
    )


def write_sidecar(run: RunResult, directory: pathlib.Path = REPORTS_DIR) -> pathlib.Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{run.run_id}.json"
    path.write_text(sidecar(run).model_dump_json(indent=1))
    return path


# --- Statistics (S21.07): cluster bootstrap, design effect, MDE, Clopper-Pearson --------------------------
Z_ALPHA_2 = 1.959964  # two-sided 5%
Z_POWER_80 = 0.841621
Z_SUM = Z_ALPHA_2 + Z_POWER_80  # ~2.80


def cluster_ci(
    values: Sequence[float],
    clusters: Sequence[Hashable],
    seed: int = 7,
    n_boot: int = 2_000,
) -> tuple[float, float, float]:
    """(mean, low, high): 95% percentile CI of the mean from a seeded cluster bootstrap (whole clusters, e.g.
    `source_doc`, are resampled with replacement). Singleton clusters give the ordinary item bootstrap."""
    if not values:
        return 0.0, 0.0, 0.0
    groups: dict[Hashable, list[float]] = defaultdict(list)
    for v, c in zip(values, clusters, strict=True):
        groups[c].append(v)
    sums = [sum(g) for g in groups.values()]
    cnts = [len(g) for g in groups.values()]
    g_n = len(sums)
    rng = random.Random(seed)  # noqa: S311 (statistics, not security)
    means: list[float] = []
    for _ in range(n_boot):
        idx = [rng.randrange(g_n) for _ in range(g_n)]
        means.append(sum(sums[i] for i in idx) / sum(cnts[i] for i in idx))
    means.sort()
    return sum(values) / len(values), means[int(0.025 * n_boot)], means[min(n_boot - 1, int(0.975 * n_boot))]


def design_effect(values: Sequence[float], clusters: Sequence[Hashable]) -> float:
    """Cluster-robust variance of the mean over the naive iid variance (1.0 = no clustering)."""
    n = len(values)
    groups: dict[Hashable, list[float]] = defaultdict(list)
    for v, c in zip(values, clusters, strict=True):
        groups[c].append(v)
    g = len(groups)
    if n < 2 or g < 2:
        return 1.0
    mean = sum(values) / n
    naive = sum((v - mean) ** 2 for v in values) / (n - 1) / n
    if naive == 0:
        return 1.0
    clustered = g / (g - 1) * sum((sum(x - mean for x in grp)) ** 2 for grp in groups.values()) / n**2
    return clustered / naive


def mde(var_diff: float, n: int, deff: float = 1.0) -> float:
    """Minimum detectable mean difference at 5% two-sided alpha and 80% power: 2.8 * sqrt(var * deff / n).
    For binary paired outcomes var ~ the discordant share d, giving ~2.8 * sqrt(d / n)."""
    return Z_SUM * math.sqrt(var_diff * deff / n) if n > 0 else float("inf")


def clopper_pearson_upper(errors: int, n: int, alpha: float = 0.05) -> float:
    """One-sided (1 - alpha) exact upper bound on an error rate: the p with P(X <= errors | n, p) = alpha."""
    if errors >= n:
        return 1.0

    def cdf(p: float) -> float:
        return sum(math.comb(n, i) * p**i * (1 - p) ** (n - i) for i in range(errors + 1))

    lo, hi = 0.0, 1.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if cdf(mid) > alpha:
            lo = mid
        else:
            hi = mid
    return hi


def cluster_key(item_id: str, source_doc: str | None) -> str:
    return source_doc or item_id


_SCOPE_V1 = (
    "- scope: deterministic graders only (no LLM judge); labelled pre-review while every item is `draft`"
)
_SCOPE_V2 = (
    "- scope: v2 claim/support/abstention judges plus deterministic graders; items `draft` until audited"
)


def _f(v: float | None) -> str:
    if v is None:
        return "-"
    return str(int(v)) if float(v).is_integer() and abs(v) >= 2 else f"{v:.3f}"


def _iso(t: dt.datetime | None) -> str:
    return t.isoformat() if t else "-"


def _cell(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ")


def render(run: RunResult) -> str:
    metrics = run.metric_names()
    lines = [
        f"# Eval run {run.run_id}",
        "",
        f"- name: `{run.name}`  suite: `{run.suite}`  split: `{run.split}`  git: `{run.git_sha}`",
        f"- started: {run.started_at.isoformat()}  finished: {_iso(run.finished_at)}",
        f"- items: {len(run.items)}  errored: {run.n_errors}",
        _SCOPE_V2 if run.config.get("dataset") == "v2" else _SCOPE_V1,
        "",
        "## Totals",
        "",
        "| metric | n | mean | 95% CI (clustered by source_doc) |",
        "|---|---:|---:|---|",
    ]
    tot = run.totals()
    for m, (mean, n) in tot.items():
        pairs = [
            (v, cluster_key(i.item_id, i.source_doc))
            for i in run.items
            if (v := i.metrics.get(m)) is not None
        ]
        _, lo, hi = cluster_ci([v for v, _ in pairs], [c for _, c in pairs])
        lines.append(f"| {m} | {n} | {_f(mean)} | [{_f(lo)}, {_f(hi)}] |")
    strata = sorted({i.stratum for i in run.items if i.stratum})
    if strata:
        lines += ["", "## By stratum (diagnostic)", "", "| stratum | items | " + " | ".join(metrics) + " |"]
        lines.append("|---|---:|" + "---:|" * len(metrics))
        for st in strata:
            sub = [i for i in run.items if i.stratum == st]
            cells = []
            for m in metrics:
                vs = [i.metrics[m] for i in sub if m in i.metrics]
                cells.append(_f(sum(vs) / len(vs)) if vs else "-")
            lines.append(f"| {st} | {len(sub)} | " + " | ".join(cells) + " |")
    cats = sorted({i.category for i in run.items})
    if len(cats) > 1:
        lines += ["", "## By category", "", "| category | items | " + " | ".join(metrics) + " |"]
        lines.append("|---|---:|" + "---:|" * len(metrics))
        for c in cats:
            t = run.totals(c)
            n = sum(1 for i in run.items if i.category == c)
            lines.append(
                f"| {c} | {n} | " + " | ".join(_f(t[m][0]) if m in t else "-" for m in metrics) + " |"
            )
    lines += ["", "## Per item", "", "| item | category | split | " + " | ".join(metrics) + " | error |"]
    lines.append("|---|---|---|" + "---:|" * len(metrics) + "---|")
    for i in run.items:
        for r in [i, *i.turns]:
            lines.append(
                f"| {_cell(r.item_id)} | {r.category} | {r.split} | "
                + " | ".join(_f(r.metrics.get(m)) for m in metrics)
                + f" | {_cell(r.error or '')} |"
            )
    return "\n".join(lines) + "\n"


def write_report(run: RunResult, directory: pathlib.Path = REPORTS_DIR) -> pathlib.Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{run.run_id}.md"
    path.write_text(render(run))
    return path
