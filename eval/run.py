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
import importlib
import math
import pathlib
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.ids import uuid7
from app.db.models.eval import EvalResult, EvalRun
from eval import baseline as bl
from eval.graders import grade_item
from eval.mapping import ChunkInfo, map_item
from eval.multiturn import NewSession, Pipeline, run_multiturn
from eval.report import REPORTS_DIR, ItemResult, RunResult, write_report, write_sidecar
from eval.schema import EvalContext, GoldenItem, ItemOutput, Query

SUITES = ("retrieval", "full", "smoke")

__all__ = ["Harness", "ItemOutput", "Pipeline", "Query", "main", "run_suite"]


@dataclass
class Harness:
    pipeline: Pipeline
    chunks_by_doc: Mapping[str, Sequence[ChunkInfo]]  # eval document key -> the ingested chunks
    ctx: EvalContext = field(default_factory=EvalContext)
    new_session: NewSession | None = None


def degraded_abort(run: RunResult, max_rate: float) -> str | None:
    """The ABORT line when too many items ran in a degraded configuration, else None."""
    rate = run.degraded_rate()
    if rate > max_rate:
        return f"ABORT: degraded_rate {rate:.3f} > {max_rate:.3f} ({run.degraded_by_kind()}); results are invalid"
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
    return ItemResult(
        item.id, item.category, split, score(item, out), err, quote_hits=hits, degraded=out.degraded
    )


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


def _load_setup(spec: str) -> Callable[[str], Awaitable[Harness]]:
    mod, _, attr = spec.partition(":")
    if not attr:
        raise SystemExit("--pipeline must be module:callable")
    fn: Callable[[str], Awaitable[Harness]] = getattr(importlib.import_module(mod), attr)
    return fn


async def _main(a: argparse.Namespace) -> int:
    from app.db.session import get_sessionmaker
    from eval.loader import load_dataset

    ds = load_dataset()
    split = "smoke" if a.suite == "smoke" else a.split
    items = ds.select(split)
    t_wall = time.monotonic()
    harness = await _load_setup(a.pipeline)(a.config)
    sm = None if a.no_db else get_sessionmaker()
    run, path = await run_suite(
        items, harness, name=a.config, suite=a.suite, split=split, split_of=ds.split_of, sm=sm
    )
    print(f"wall_s {time.monotonic() - t_wall:.1f}")
    print(f"degraded_rate {run.degraded_rate():.3f} {run.degraded_by_kind()}")
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
    p.add_argument("--split", choices=("train", "test", "smoke", "all"), default="all")
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
    p.add_argument("--no-db", action="store_true", help="skip eval_runs/eval_results rows")
    return asyncio.run(_main(p.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
