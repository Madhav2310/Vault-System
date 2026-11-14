"""Eval runner (plan S2.12). Iterates golden items, calls a pluggable pipeline, grades deterministically,
writes `eval_runs` / `eval_results` (one row per item per metric) and `eval/reports/<run_id>.md`.

PIPELINE PROTOCOL (the real RAG pipeline plugs in here; tests use a scripted toy):

    async def pipeline(query: Query, ctx: EvalContext) -> ItemOutput

`Query` (eval.schema) is the gold-free view of one question: item_id, question, category,
scope_docs (eval document keys, None = whole vault), turn_index / n_turns (multi-turn items ask turn 0..N-1
in order through the SAME `ctx.session_id`; `ctx.history` holds the earlier (Query, ItemOutput) pairs).
`EvalContext` carries owner_id, `doc_ids` (eval document key -> ingested document id) and free-form services.
`ItemOutput` (eval.schema) is what the pipeline returns:

    retrieved:   list[str]            ranked chunk ids (strings), best first; the top 40 if available
    segments:    list[AnswerSegment]  answer text, each with citations: CitedRange(chunk_id, sentence_start,
                                      sentence_end), sentence indexes half-open within the chunk
    abstained:   bool                 the system declined to answer (must not also cite: see graders)
    meta:        str | list[str] | None   structured result of a vault-metadata route (exact_meta: value as
                                      a string; set_meta: the list of document titles)
    conflict_flagged: bool | None     the gate's conflict decision, for conflict items
    latency_ms, cost_usd: float | None   optional; reported as lower-is-better metrics when set

An exception from the pipeline is caught per item: the item is scored as an empty output and the error is
recorded in the report (errors are never dropped from the denominators).

A run is built from a `Harness` (pipeline + the ingested chunks used for gold mapping + base context). The CLI
loads one from `--pipeline module:setup`, where `async def setup(config: str) -> Harness`.

CLI (from the repo root's backend/ with the eval group, which is a default uv group):

    cd backend && PYTHONPATH=.. uv run --frozen python -m eval.run --suite retrieval --config baseline \\
        --pipeline my_module:setup [--split train|test|smoke|all] [--freeze NAME] [--compare NAME]

Suites: `retrieval` = retrieval metrics only; `full` = retrieval + deterministic answer metrics; `smoke` =
`full` on the smoke split. Default split: `all` (`smoke` for the smoke suite).
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import datetime as dt
import functools
import importlib
import inspect
import math
import pathlib
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.ids import uuid7
from app.db.models.eval import EvalResult, EvalRun
from eval import baseline as bl
from eval.graders import grade_item
from eval.mapping import ChunkInfo, GoldTarget, map_item
from eval.multiturn import NewSession, Pipeline, run_multiturn
from eval.report import REPORTS_DIR, ItemResult, RunResult, write_report, write_sidecar
from eval.schema import EvalContext, GoldenItem, ItemOutput, Query

SUITES = ("retrieval", "full", "smoke")

__all__ = ["Harness", "ItemOutput", "Pipeline", "Query", "main", "run_suite"]


ExtraScorer = Callable[
    [GoldenItem, ItemOutput, Sequence[GoldTarget], Mapping[str, ChunkInfo]], Awaitable[dict[str, float]]
]


@dataclass
class Harness:
    pipeline: Pipeline
    chunks_by_doc: Mapping[str, Sequence[ChunkInfo]]  # eval document key -> the ingested chunks
    ctx: EvalContext = field(default_factory=EvalContext)
    new_session: NewSession | None = None
    # extra per-item metrics (answer judges): async (item, output, targets, chunks) -> metrics
    extra_scorer: ExtraScorer | None = None
    # total spend so far (USD) for `--max-usd`: pipeline answering + judges
    spent: Callable[[], Awaitable[float]] | None = None
    close: Callable[[], Awaitable[None]] | None = None  # release clients (HTTP) when the run ends
    # v2 runs (eval.run_v2): the judges' gateway + spend guard, and document key -> document sentence texts
    # (the stream gold_evidence ranges index; None = unavailable)
    judge_gw: Any = None
    judge_guard: Any = None
    gold_units: Callable[[str], Sequence[str] | None] | None = None
    # the answer-side config the server recorded for this run's turns (eval.fingerprint.merge_answer_config)
    answer_config: Callable[[], Awaitable[dict[str, Any] | None]] | None = None


def select_filter(items: Sequence[GoldenItem], spec: str | None) -> list[GoldenItem]:
    """`--filter scope_docs=N`: keep items whose scope names exactly N documents."""
    if not spec:
        return list(items)
    key, _, val = spec.partition("=")
    if key != "scope_docs" or not val.isdigit():
        raise SystemExit(f"unsupported --filter {spec!r} (use scope_docs=N)")
    return [i for i in items if len(i.doc_ids or ()) == int(val)]


def degraded_abort(run: RunResult, max_rate: float) -> str | None:
    """The ABORT line when too many items ran in a degraded configuration, else None."""
    rate = run.degraded_rate()
    if rate > max_rate:
        kinds = run.degraded_by_kind()
        return f"ABORT: degraded_rate {rate:.3f} > {max_rate:.3f} ({kinds}); results are invalid"
    return None


def _git_sha() -> str:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
            cwd=pathlib.Path(__file__).parent,
        )
        return r.stdout.strip() or "unknown"
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _extra(out: ItemOutput) -> dict[str, float]:
    m: dict[str, float] = {}
    if out.latency_ms is not None:
        m["latency_ms"] = float(out.latency_ms)
    if out.ttft_ms is not None:
        m["ttft_ms"] = float(out.ttft_ms)
    if out.cost_usd is not None:
        m["cost_usd"] = float(out.cost_usd)
    return m


async def _run_item(
    item: GoldenItem, h: Harness, chunks: Mapping[str, ChunkInfo], split: str, *, answers: bool
) -> ItemResult:
    def score(it: GoldenItem, out: ItemOutput) -> dict[str, float]:
        m = grade_item(it, out, map_item(it, h.chunks_by_doc), chunks, answers=answers)
        return {**m, **_extra(out)}

    if item.category == "multi_turn":
        return await run_multiturn(item, h.pipeline, h.ctx, score, split=split, new_session=h.new_session)
    q = Query(item.id, item.question or "", item.category, tuple(item.doc_ids) if item.doc_ids else None)
    err: str | None = None
    try:
        out = await h.pipeline(q, dataclasses.replace(h.ctx, history=[]))
    except Exception as e:  # scored as an empty output; the error stays visible in the report
        out, err = ItemOutput(), f"{type(e).__name__}: {e}"
    targets = [x for x in map_item(item, h.chunks_by_doc) if x.mapped]
    hits = [bool(x.chunk_ids & set(out.retrieved[:8])) for x in targets] or None
    metrics = score(item, out)
    if h.extra_scorer is not None and err is None:
        metrics.update(await h.extra_scorer(item, out, map_item(item, h.chunks_by_doc), chunks))
    return ItemResult(item.id, item.category, split, metrics, err, quote_hits=hits, degraded=out.degraded)


async def run_suite(
    items: Sequence[GoldenItem],
    harness: Harness,
    *,
    name: str,
    suite: str,
    split: str,
    split_of: Callable[[str], str] = lambda _id: "-",
    sm: async_sessionmaker[AsyncSession] | None = None,
    report_dir: pathlib.Path = REPORTS_DIR,
    git_sha: str | None = None,
    max_usd: float | None = None,
) -> tuple[RunResult, pathlib.Path]:
    """Run `items` sequentially (deterministic order). Returns the result and the report path."""
    if suite not in SUITES:
        raise ValueError(f"unknown suite {suite!r}")
    run = RunResult(
        uuid7(),
        name,
        suite,
        split,
        git_sha or _git_sha(),
        dt.datetime.now(dt.UTC),
        config={"name": name, "suite": suite, "split": split, "n_items": len(items)},
    )
    if sm is not None:
        async with sm() as s:
            s.add(EvalRun(id=run.run_id, git_sha=run.git_sha, config=run.config, started_at=run.started_at))
            await s.commit()
    chunks = {c.id: c for cs in harness.chunks_by_doc.values() for c in cs}
    for item in items:
        if max_usd is not None and harness.spent is not None and await harness.spent() >= max_usd:
            run.config["stopped"] = "budget"
            break
        run.items.append(
            await _run_item(item, harness, chunks, split_of(item.id), answers=suite != "retrieval")
        )
    run.finished_at = dt.datetime.now(dt.UTC)
    if sm is not None:
        await _persist(sm, run)
    write_sidecar(run, report_dir)
    return run, write_report(run, report_dir)


async def _persist(sm: async_sessionmaker[AsyncSession], run: RunResult) -> None:
    async with sm() as s:
        row = await s.get(EvalRun, run.run_id)
        assert row is not None
        row.finished_at = run.finished_at
        for it in run.items:
            for r in [it, *it.turns]:
                for metric, value in sorted(r.metrics.items()):
                    if not math.isfinite(value):
                        raise ValueError(f"non-finite metric {metric} for {r.item_id}")
                    s.add(
                        EvalResult(eval_run_id=run.run_id, question_id=r.item_id, metric=metric, value=value)
                    )
        await s.commit()


def _load_setup(spec: str, *, stream: bool = False) -> Callable[[str], Awaitable[Harness]]:
    mod, _, attr = spec.partition(":")
    if not attr:
        raise SystemExit("--pipeline must be module:callable")
    fn: Callable[..., Awaitable[Harness]] = getattr(importlib.import_module(mod), attr)
    if not stream:
        return fn
    if "stream" not in inspect.signature(fn).parameters:
        raise SystemExit(f"--stream: {spec} does not support streaming")
    return functools.partial(fn, stream=True)


def ttft_summary(run: RunResult) -> str | None:
    """`ttft_ms p50 <n> p95 <n> (n=<k>)` over the items that streamed a segment; None when none did."""
    vals = sorted(
        m["ttft_ms"] for i in run.items for r in (i.turns or [i]) if (m := r.metrics) and "ttft_ms" in m
    )
    if not vals:
        return None
    p50 = vals[(len(vals) - 1) // 2]
    p95 = vals[min(len(vals) - 1, math.ceil(0.95 * len(vals)) - 1)]
    return f"ttft_ms p50 {p50:.0f} p95 {p95:.0f} (n={len(vals)})"


async def _main(a: argparse.Namespace) -> int:
    if getattr(a, "dataset", "v1") == "v2":
        from eval.run_v2 import main_v2

        return await main_v2(a)
    if a.split == "dev":
        raise SystemExit("--split dev exists for --dataset v2 only (v1 has train / test / smoke)")
    from app.db.session import get_sessionmaker
    from eval.loader import load_dataset

    ds = load_dataset()
    split = "smoke" if a.suite == "smoke" else a.split
    items = select_filter(ds.select(split), a.filter)
    t_wall = time.monotonic()
    stream = getattr(a, "stream", False)
    harness = await (_load_setup(a.pipeline, stream=True) if stream else _load_setup(a.pipeline))(a.config)
    sm = None if a.no_db else get_sessionmaker()
    try:
        run, path = await run_suite(
            items,
            harness,
            name=a.config,
            suite=a.suite,
            split=split,
            split_of=ds.split_of,
            sm=sm,
            max_usd=a.max_usd,
        )
    finally:
        if harness.close is not None:
            await harness.close()
    print(f"wall_s {time.monotonic() - t_wall:.1f}")
    print(f"degraded_rate {run.degraded_rate():.3f} {run.degraded_by_kind()}")
    if stream:
        print(ttft_summary(run) or "ttft_ms: no streamed segment events")
    if run.config.get("stopped") == "budget":
        print(f"stopped: budget (cap ${a.max_usd}); partial run, nothing frozen")
        return 3
    if a.suite != "retrieval":
        from eval.metrics.answer import render_ci

        print(render_ci(run))
    if a.suite == "retrieval" and (abort := degraded_abort(run, a.max_degraded_rate)):
        print(abort)
        return 3  # no baseline is frozen from an invalid run
    print(f"run {run.run_id}: {len(run.items)} items, {run.n_errors} errored; report {path}")
    for m, (mean, n) in run.totals().items():
        print(f"  {m:24s} n={n:<4d} mean={mean:.4f}")
    if a.freeze:
        print("frozen:", bl.freeze(run, a.freeze))
    if a.compare:
        cmp = bl.compare_to_baseline(run, bl.BASELINES_DIR / f"{a.compare}.json", a.tolerance)
        for r in cmp.regressions:
            print("REGRESSION", r)
        return 0 if cmp.ok else 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m eval.run")
    p.add_argument("--suite", choices=SUITES, required=True)
    p.add_argument(
        "--config", required=True, help="config name recorded with the run and passed to the setup"
    )
    p.add_argument("--pipeline", required=True, help="module:setup, an async (config) -> Harness")
    p.add_argument("--split", choices=("train", "dev", "test", "smoke", "all"), default="all")
    p.add_argument(
        "--dataset",
        choices=("v1", "v2"),
        default="v1",
        help="v2: claim-level judges and attribution over vault_eval_v2 (train/dev/test by source_doc)",
    )
    p.add_argument(
        "--answer-concurrency",
        type=int,
        default=2,
        help="v2: items answered at once (the judges run in one bounded gather afterwards)",
    )
    p.add_argument("--freeze", metavar="NAME", help="write eval/baselines/NAME.json from this run")
    p.add_argument(
        "--compare", metavar="NAME", help="exit 1 if a metric regressed vs eval/baselines/NAME.json"
    )
    p.add_argument("--tolerance", type=float, default=bl.DEFAULT_TOLERANCE)
    p.add_argument(
        "--max-degraded-rate",
        type=float,
        default=0.05,
        help="retrieval suite: abort (exit 3, no freeze) above this share of degraded items",
    )
    p.add_argument("--max-usd", type=float, default=None, help="stop before an item once spend reaches this")
    p.add_argument("--filter", default=None, help="only items matching, e.g. scope_docs=1")
    p.add_argument(
        "--stream",
        action="store_true",
        help="answer over the SSE stream and report TTFT (p50/p95); needs a setup with a `stream` parameter",
    )
    p.add_argument("--no-db", action="store_true", help="skip eval_runs/eval_results rows")
    return asyncio.run(_main(p.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
