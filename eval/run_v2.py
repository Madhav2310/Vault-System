"""Answer-eval runner for the v2 dataset (S21.10a): `python -m eval.run --dataset v2 ...` lands here.

Flow, per run:

1. Select live items (`audit_status: dropped` never runs) of one split (`splits_v2`: train / dev / test by
   `source_doc` hash) and optionally filter (`--filter stratum=injection`).
2. Phase 1, answering: every item goes through the harness pipeline (multi-turn items ask `prior_turns` first,
   in the same session, then the scored question; scope = `doc_ids`). Spend is checked before each item.
3. Phase 2, scoring: gold sentence ranges (document sentence stream) are mapped to chunk sentences by text
   (`eval.mapping.map_quote`); deterministic graders run; the judges (`eval.judge` v2) run concurrently
   through `gather_bounded`; the per-item `attribution` signals are recorded for `eval.attribution`.
4. `--freeze NAME` writes `eval/baselines/NAME.json` (`freeze_v2`): means with clustered CIs, per-stratum
   table (diagnostic), MDE inputs, attribution counts per stage.

Metrics (per item, only where defined): `claim_completeness`, `answer_pass` (all required claims entailed;
for abstain items the abstention judge's verdict), `support_rate`, `abstention_correct` (strata
unanswerable / false_premise / versioned), `must_not_contain_ok`, `injection_violation` (injection stratum),
`citation_integrity`, `cite_sent_precision` / `cite_sent_recall` / `cite_chunk_*`, `recall@8`, `mrr@40`,
`latency_ms`, `ttft_ms`, `cost_usd`. Judges never see the system's model name (see `eval.judge`).
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import datetime as dt
import inspect
import json
import math
import pathlib
import time
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.ids import uuid7
from eval import baseline as bl
from eval.attribution import STAGES, UNKNOWN, from_sidecar_item, stage_table
from eval.graders import (
    citation_metrics,
    citations_of,
    injection_violation,
    must_not_contain_ok,
    retrieval_metrics,
)
from eval.judge import (
    CONCURRENCY,
    ClaimJudgement,
    JudgeOutputError,
    SpendCapReached,
    SupportJudgement,
    abstain_applicable,
    build_case,
    gather_bounded,
    judge_abstention,
    judge_claims,
    judge_support,
)
from eval.mapping import ChunkInfo, GoldTarget, map_quote
from eval.report import (
    REPORTS_DIR,
    ItemResult,
    RunResult,
    cluster_ci,
    cluster_key,
    design_effect,
    mde,
    sidecar,
    write_report,
    write_sidecar,
)
from eval.run import SUITES, Harness, _extra, _git_sha, _persist, ttft_summary
from eval.schema import GoldenItem, GoldItemV2, ItemOutput, Query
from eval.splits_v2 import SPLITS_V2, live_items, select_v2, split_counts, split_of

DATASET = "v2"
BASELINE_NAME = "answer_v2"
# attribution keys the runner records (eval.attribution.AttributionInput names) minus identity fields
ATTR_KEYS = (
    "should_abstain",
    "abstained",
    "gold_in_parsed",
    "gold_in_chunk",
    "gold_in_candidates",
    "gold_in_evidence",
    "gold_cited",
    "cited_outside_gold",
    "unresolved_citations",
    "claim_missing_or_unsupported",
    "judge_verdict",
    "human_label",
)


@dataclass
class ItemOutputV2(ItemOutput):
    """`ItemOutput` plus the chunk ids the generator was given (selected candidates and citations)."""

    evidence: list[str] | None = None


# --- selection --------------------------------------------------------------------------------------------


def select_filter_v2(items: Sequence[GoldItemV2], spec: str | None) -> list[GoldItemV2]:
    """`--filter stratum=a,b` | `scope_docs=N` | `source_doc=key`."""
    if not spec:
        return list(items)
    key, _, val = spec.partition("=")
    if key == "stratum" and val:
        wanted = set(val.split(","))
        return [i for i in items if i.stratum in wanted]
    if key == "scope_docs" and val.isdigit():
        return [i for i in items if len(i.doc_ids or ()) == int(val)]
    if key == "source_doc" and val:
        return [i for i in items if i.source_doc == val]
    raise SystemExit(f"unsupported --filter {spec!r} (use stratum=a,b | scope_docs=N | source_doc=key)")


# --- v1 views (the deterministic graders speak GoldenItem) ------------------------------------------------


def v1_view(item: GoldItemV2) -> GoldenItem:
    """A grading-only `GoldenItem` view of a v2 item: scope, must_not_contain and category are what the
    deterministic graders read. Unvalidated on purpose (like `GoldenItem.turn_item`)."""
    unans = item.answer_type == "abstain"
    return GoldenItem.model_construct(
        id=item.id,
        source="v2",
        category="unanswerable" if unans else ("injection" if item.stratum == "injection" else "lookup"),
        doc_ids=item.doc_ids,
        expected_coverage="insufficient" if unans else "supported",
        grading="abstention" if unans else "judge",
        status=item.audit_status,
        question=item.question,
        must_not_contain=item.must_not_contain or None,
    )


def gold_targets(
    item: GoldItemV2,
    chunks_by_doc: Mapping[str, Sequence[ChunkInfo]],
    units_of: Callable[[str], Sequence[str] | None] | None,
) -> tuple[list[GoldTarget], bool]:
    """One target per `gold_evidence` range: the range's sentences joined, located in the chunks of that
    document. Returns (targets, units_ok); units_ok is False when any range could not be turned into text
    (then gold-based signals are unknown, not failures)."""
    out: list[GoldTarget] = []
    ok = True
    for g in item.gold_evidence:
        units = units_of(g.doc) if units_of is not None else None
        if units is None or g.sentence_end > len(units):
            ok = False
            out.append(GoldTarget(item.id, g.doc, "", None, (), "document sentences unavailable"))
        elif not chunks_by_doc.get(g.doc):
            out.append(GoldTarget(item.id, g.doc, "", None, (), "document has no ingested chunks"))
        else:
            quote = " ".join(units[g.sentence_start : g.sentence_end])
            out.append(map_quote(quote, None, chunks_by_doc[g.doc], item_id=item.id, doc_id=g.doc))
    return out, ok


# --- deterministic metrics and attribution signals --------------------------------------------------------


def _bad_citations(out: ItemOutput, chunks: Mapping[str, ChunkInfo]) -> int:
    """Citations naming an unknown chunk or a sentence range outside it (a proxy for CITATION_UNRESOLVED: the
    API drops truly unresolved handles before storage, so those are not visible over HTTP)."""
    n = 0
    for c in citations_of(out):
        ci = chunks.get(c.chunk_id)
        if ci is None or not 0 <= c.sentence_start < c.sentence_end <= len(ci.sentences):
            n += 1
    return n


def deterministic_metrics(
    item: GoldItemV2,
    out: ItemOutput,
    targets: Sequence[GoldTarget],
    chunks: Mapping[str, ChunkInfo],
    *,
    answers: bool,
) -> dict[str, float]:
    v1 = v1_view(item)
    mapped = [t for t in targets if t.mapped]
    m: dict[str, float] = {}
    if mapped:
        m.update(retrieval_metrics(mapped, out.retrieved))
    if not answers:
        return m
    m.update(citation_metrics(v1, out, targets, chunks))
    if item.must_not_contain:
        m["must_not_contain_ok"] = must_not_contain_ok(item.must_not_contain, out)
    if item.stratum == "injection":
        m["injection_violation"] = injection_violation(v1, out, chunks)
    m["abstained"] = float(out.abstained)
    return m


def attribution_signals(
    item: GoldItemV2,
    out: ItemOutput,
    targets: Sequence[GoldTarget],
    units_ok: bool,
    chunks: Mapping[str, ChunkInfo],
    metrics: Mapping[str, float],
    *,
    claims: ClaimJudgement | None,
    support: SupportJudgement | None,
    error: str | None,
) -> dict[str, Any]:
    """The per-item `attribution` object (`eval.attribution.AttributionInput` names). None = not recorded."""
    should_abstain = item.answer_type == "abstain"
    mapped = [t for t in targets if t.mapped]
    cited = {c.chunk_id for c in citations_of(out)}
    acceptable = set().union(*(t.chunk_ids for t in mapped)) if mapped else set()
    evidence = getattr(out, "evidence", None)
    gold_known = bool(targets) and units_ok and not should_abstain
    sig: dict[str, Any] = {k: None for k in ATTR_KEYS}
    sig["should_abstain"] = should_abstain
    sig["abstained"] = out.abstained
    if gold_known:
        sig["gold_in_chunk"] = all(t.mapped for t in targets)
        if mapped:
            ret = set(out.retrieved)
            sig["gold_in_candidates"] = all(bool(t.chunk_ids & ret) for t in mapped)
            if evidence is not None:
                ev = set(evidence)
                sig["gold_in_evidence"] = all(bool(t.chunk_ids & ev) for t in mapped)
            sig["gold_cited"] = any(bool(t.chunk_ids & cited) for t in mapped)
            sig["cited_outside_gold"] = bool(cited - acceptable)
    sig["unresolved_citations"] = _bad_citations(out, chunks)
    unsupported = support is not None and any(s is False for s in support.supported)
    if claims is not None:
        sig["claim_missing_or_unsupported"] = (not claims.answer_pass) or unsupported
    if "answer_pass" in metrics:
        sig["judge_verdict"] = "pass" if metrics["answer_pass"] == 1.0 else "fail"
    sig["failed"] = bool(
        error
        or metrics.get("answer_pass") == 0.0
        or metrics.get("abstention_correct") == 0.0
        or metrics.get("injection_violation") == 1.0
        or metrics.get("must_not_contain_ok") == 0.0
        or metrics.get("citation_integrity", 1.0) < 1.0
    )
    return sig


# --- judging ----------------------------------------------------------------------------------------------


class _BudgetStop(Exception):
    """The run's `--max-usd` was reached before this judge call."""


@dataclass
class Judged:
    metrics: dict[str, float] = field(default_factory=dict)
    claims: ClaimJudgement | None = None
    support: SupportJudgement | None = None
    errors: list[str] = field(default_factory=list)
    budget_stop: bool = False


async def _judge_all(
    pending: Sequence[tuple[GoldItemV2, ItemOutput, Mapping[str, ChunkInfo]]],
    harness: Harness,
    *,
    max_usd: float | None,
    limit: int,
) -> list[Judged]:
    """Every judge call of every item in ONE bounded gather (calls, not items, are the concurrency unit)."""
    gw, guard = harness.judge_gw, harness.judge_guard
    results = [Judged() for _ in pending]
    calls: list[Any] = []
    tags: list[tuple[int, str]] = []

    async def call(fn: Callable[..., Awaitable[Any]], *args: Any) -> tuple[bool, Any]:
        try:
            if max_usd is not None and harness.spent is not None and await harness.spent() >= max_usd:
                raise _BudgetStop
            return True, await fn(gw, *args, guard=guard)
        except Exception as e:  # recorded per item; one bad judge call must not sink the run
            return False, e

    for n, (item, out, chunks) in enumerate(pending):
        answer = out.answer_text.strip()
        r = results[n]
        if item.required_claims:
            if answer:
                calls.append(call(judge_claims, item, out.answer_text))
                tags.append((n, "claims"))
            else:  # nothing to judge: an empty answer entails no claim (no model call)
                r.claims = ClaimJudgement(item.id, tuple(False for _ in item.required_claims), Decimal(0))
        if abstain_applicable(item):
            if answer:
                calls.append(call(judge_abstention, item, out.answer_text))
                tags.append((n, "abstain"))
            else:
                r.metrics["abstention_correct"] = float(item.answer_type == "abstain" and out.abstained)
        views = build_case(v1_view(item), out, chunks).segments
        if any(s.passages for s in views):
            calls.append(call(judge_support, views))
            tags.append((n, "support"))
    done: list[tuple[bool, Any]] = await gather_bounded(calls, limit)
    for (n, kind), (ok, val) in zip(tags, done, strict=True):
        r = results[n]
        if not ok:
            if isinstance(val, _BudgetStop | SpendCapReached):
                r.budget_stop = True
            else:
                r.errors.append(f"judge_{kind}: {type(val).__name__}: {val}")
            continue
        if kind == "claims":
            r.claims = val
        elif kind == "support":
            r.support = val
        else:
            r.metrics["abstention_correct"] = float(val.correct)
    for r in results:
        if r.claims is not None:
            r.metrics["claim_completeness"] = r.claims.completeness
            r.metrics["answer_pass"] = float(r.claims.answer_pass)
        if r.support is not None and not math.isnan(r.support.rate):
            r.metrics["support_rate"] = r.support.rate
    for (item, _out, _c), r in zip(pending, results, strict=True):
        if item.answer_type == "abstain" and "abstention_correct" in r.metrics:
            r.metrics["answer_pass"] = r.metrics[
                "abstention_correct"
            ]  # no claims: the judge's verdict is the pass
    return results


# --- phase 1: answering -----------------------------------------------------------------------------------


@dataclass
class Answered:
    item: GoldItemV2
    out: ItemOutput
    error: str | None
    cost_usd: float


async def _answer(item: GoldItemV2, h: Harness) -> Answered:
    scope = tuple(item.doc_ids) if item.doc_ids else None
    turns = [*item.prior_turns, item.question]
    n = len(turns)
    ctx = dataclasses.replace(h.ctx, history=[])
    cost = 0.0
    out = ItemOutput()
    err: str | None = None
    try:
        if n > 1 and h.new_session is not None:  # one session for the whole conversation
            ctx.session_id = await h.new_session(Query(item.id, turns[0], item.stratum, scope, 0, n), ctx)
        for i, text in enumerate(turns):
            q = Query(item.id, text, item.stratum, scope, i, n)
            try:
                out = await h.pipeline(q, ctx)
            except Exception as e:
                out, err = ItemOutput(), f"turn {i + 1}: {type(e).__name__}: {e}"
                break
            cost += out.cost_usd or 0.0
            ctx.history.append((q, out))
    except Exception as e:  # session creation failed
        out, err = ItemOutput(), f"{type(e).__name__}: {e}"
    return Answered(item, out, err, cost)


async def _answer_all(
    items: Sequence[GoldItemV2], h: Harness, *, max_usd: float | None, concurrency: int
) -> tuple[list[Answered], bool]:
    """Answer in item order; a worker checks the cap before it takes the next item, so overshoot is bounded
    by `concurrency` items. Returns (answered prefix, stopped_on_budget)."""
    results: dict[int, Answered] = {}
    nxt = 0
    stopped = False

    async def worker() -> None:
        nonlocal nxt, stopped
        while not stopped:
            if nxt >= len(items):
                return
            if max_usd is not None and h.spent is not None and await h.spent() >= max_usd:
                stopped = True
                return
            i = nxt
            nxt += 1
            results[i] = await _answer(items[i], h)

    await asyncio.gather(*(worker() for _ in range(max(1, concurrency))))
    return [results[i] for i in sorted(results)], stopped


# --- the run ----------------------------------------------------------------------------------------------


async def run_suite_v2(
    items: Sequence[GoldItemV2],
    harness: Harness,
    *,
    name: str,
    suite: str,
    split: str,
    sm: async_sessionmaker[AsyncSession] | None = None,
    report_dir: pathlib.Path = REPORTS_DIR,
    git_sha: str | None = None,
    max_usd: float | None = None,
    answer_concurrency: int = 2,
    judge_concurrency: int = CONCURRENCY,
) -> tuple[RunResult, pathlib.Path]:
    if suite not in SUITES:
        raise ValueError(f"unknown suite {suite!r}")
    answers = suite != "retrieval"
    if answers and (harness.judge_gw is None):
        raise ValueError("v2 answer suites need harness.judge_gw (the judges' gateway)")
    items = live_items(items)
    run = RunResult(
        uuid7(),
        name,
        suite,
        split,
        git_sha or _git_sha(),
        dt.datetime.now(dt.UTC),
        config={"name": name, "suite": suite, "split": split, "n_items": len(items), "dataset": DATASET},
    )
    if sm is not None:
        from app.db.models.eval import EvalRun

        async with sm() as s:
            s.add(EvalRun(id=run.run_id, git_sha=run.git_sha, config=run.config, started_at=run.started_at))
            await s.commit()
    chunks = {c.id: c for cs in harness.chunks_by_doc.values() for c in cs}

    answered, stopped = await _answer_all(items, harness, max_usd=max_usd, concurrency=answer_concurrency)
    # gold sentences of every document the answered items cite as gold: one heavy parse at a time
    units: dict[str, Sequence[str] | None] = {}
    if harness.gold_units is not None:
        for d in sorted({g.doc for a in answered for g in a.item.gold_evidence}):
            units[d] = await asyncio.to_thread(harness.gold_units, d)
    units_of = (lambda d: units.get(d)) if harness.gold_units is not None else None

    judged: list[Judged]
    if answers:
        # When the cap already tripped while answering, the answered items are paid for: they are still
        # scored (judge spend stays bounded by the judges' SpendGuard). A cap tripped while judging stops it.
        judged = await _judge_all(
            [(a.item, a.out, chunks) for a in answered],
            harness,
            max_usd=None if stopped else max_usd,
            limit=judge_concurrency,
        )
    else:
        judged = [Judged() for _ in answered]

    for a, j in zip(answered, judged, strict=True):
        if j.budget_stop:  # judging was cut short: the item is not scored (and not counted)
            stopped = True
            continue
        item = a.item
        targets, units_ok = gold_targets(item, harness.chunks_by_doc, units_of)
        m = deterministic_metrics(item, a.out, targets, chunks, answers=answers)
        m.update(_extra(a.out))
        if a.cost_usd:
            m["cost_usd"] = a.cost_usd  # all turns of a multi-turn item
        m.update(j.metrics)
        err = "; ".join([e for e in [a.error, *j.errors] if e]) or None
        attr = (
            attribution_signals(
                item, a.out, targets, units_ok, chunks, m, claims=j.claims, support=j.support, error=err
            )
            if answers
            else None
        )
        run.items.append(
            ItemResult(
                item.id,
                item.stratum,
                split_of(item.source_doc),
                m,
                err,
                degraded=a.out.degraded,
                source_doc=item.source_doc,
                stratum=item.stratum,
                attribution=attr,
            )
        )
    if stopped:
        run.config["stopped"] = "budget"
    if harness.spent is not None:
        run.config["spent_usd"] = round(await harness.spent(), 4)
    run.finished_at = dt.datetime.now(dt.UTC)
    if sm is not None:
        await _persist(sm, run)
    write_sidecar(run, report_dir)
    return run, write_report(run, report_dir)


# --- freeze -----------------------------------------------------------------------------------------------


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


def freeze_v2(
    run: RunResult, name: str = BASELINE_NAME, directory: pathlib.Path = bl.BASELINES_DIR
) -> pathlib.Path:
    """`eval/baselines/<name>.json`: clustered CIs, per-stratum table, MDE inputs, attribution."""
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
        for k in ATTR_KEYS
        if i.attribution.get(k) is None
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
        "dataset": DATASET,
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


# --- CLI half (called by eval.run for --dataset v2) -------------------------------------------------------


async def main_v2(a: argparse.Namespace) -> int:
    from app.db.session import get_sessionmaker
    from eval.build.build_v2_docs import load_documents_v2
    from eval.loader import load_dataset_v2

    if a.suite == "smoke":
        raise SystemExit("--dataset v2 has no smoke split: use --split dev --filter ... or a small --max-usd")
    all_items = load_dataset_v2().items
    items = select_filter_v2(select_v2(all_items, a.split), a.filter)
    print(f"v2 {a.split}: {len(items)} live items; splits (items, source_docs): {split_counts(all_items)}")
    docs = load_documents_v2()
    keys = sorted({d for i in items for d in _doc_keys(i) if docs.get(d, {}).get("kind") != "virtual"})
    fn = _setup_fn(a)
    t_wall = time.monotonic()
    harness = await fn(a.config, dataset=DATASET, doc_keys=keys)
    sm = None if a.no_db else get_sessionmaker()
    try:
        run, path = await run_suite_v2(
            items,
            harness,
            name=a.config,
            suite=a.suite,
            split=a.split,
            sm=sm,
            max_usd=a.max_usd,
            answer_concurrency=a.answer_concurrency,
        )
    finally:
        if harness.close is not None:
            await harness.close()
    print(f"wall_s {time.monotonic() - t_wall:.1f}")
    print(
        f"spent_usd {run.config.get('spent_usd')}" + (f" (cap {a.max_usd})" if a.max_usd is not None else "")
    )
    if getattr(a, "stream", False):
        print(ttft_summary(run) or "ttft_ms: no streamed segment events")
    if run.config.get("stopped") == "budget":
        print(f"stopped: budget (cap ${a.max_usd}); partial run, nothing frozen")
        return 3
    print(f"run {run.run_id}: {len(run.items)} items, {run.n_errors} errored; report {path}")
    for m, (mean, n) in run.totals().items():
        print(f"  {m:24s} n={n:<4d} mean={mean:.4f}")
    if a.freeze:
        print("frozen:", freeze_v2(run, a.freeze))
    if a.compare:
        cmp = bl.compare_to_baseline(run, bl.BASELINES_DIR / f"{a.compare}.json", a.tolerance)
        for r in cmp.regressions:
            print("REGRESSION", r)
        return 0 if cmp.ok else 1
    return 0


def _doc_keys(item: GoldItemV2) -> set[str]:
    from eval.build.build_v2_docs import item_docs

    return item_docs(item)


def _setup_fn(a: argparse.Namespace) -> Callable[..., Any]:
    from eval.run import _load_setup

    fn = _load_setup(a.pipeline, stream=getattr(a, "stream", False))
    target = fn.func if hasattr(fn, "func") else fn
    if "dataset" not in inspect.signature(target).parameters:
        raise SystemExit(f"--dataset v2: {a.pipeline} does not support v2 (needs dataset= and doc_keys=)")
    return fn


__all__ = [
    "BASELINE_NAME",
    "SPLITS_V2",
    "ItemOutputV2",
    "JudgeOutputError",
    "attribution_signals",
    "freeze_v2",
    "gold_targets",
    "main_v2",
    "run_suite_v2",
    "select_filter_v2",
]
