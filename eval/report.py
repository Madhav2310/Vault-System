"""Run results and the Markdown report (`eval/reports/<run_id>.md`): header, totals, per-category means and a
per-item table. Totals average each metric only over the items where it is defined (n is shown)."""

from __future__ import annotations

import datetime as dt
import pathlib
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

ROOT = pathlib.Path(__file__).resolve().parents[1]
REPORTS_DIR = ROOT / "eval" / "reports"
# Metrics where lower is better; everything else is higher-is-better.
LOWER_IS_BETTER = frozenset({"latency_ms", "cost_usd", "gold_unmapped"})


@dataclass
class ItemResult:
    item_id: str
    category: str
    split: str
    metrics: dict[str, float]
    error: str | None = None
    quote_hits: list[bool] | None = (
        None  # per mapped gold quote: acceptable chunk in the top 8 (paired tests)
    )
    turns: list[ItemResult] = field(default_factory=list)  # multi-turn: one per turn, ids `<id>#t<n>`


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
            )
            for i in run.items
        ],
    )


def write_sidecar(run: RunResult, directory: pathlib.Path = REPORTS_DIR) -> pathlib.Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{run.run_id}.json"
    path.write_text(sidecar(run).model_dump_json(indent=1))
    return path


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
        "- scope: deterministic graders only (no LLM judge); labelled pre-review while every item is `draft`",
        "",
        "## Totals",
        "",
        "| metric | n | mean |",
        "|---|---:|---:|",
    ]
    tot = run.totals()
    lines += [f"| {m} | {n} | {_f(mean)} |" for m, (mean, n) in tot.items()]
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
