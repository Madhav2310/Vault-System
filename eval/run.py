"""Evaluation runner: golden items through a pluggable pipeline, deterministic graders, claim-level judges.

PIPELINE PROTOCOL (the real RAG pipeline plugs in here; tests use a scripted toy):

    async def pipeline(query: Query, ctx: EvalContext) -> ItemOutput

`Query` (eval.schema) is the gold-free view of one question: item_id, question, stratum, scope_docs (eval
document keys, None = whole vault), turn_index / n_turns (a multi-turn item asks its `prior_turns` first,
then the scored question, all through the SAME `ctx.session_id`; `ctx.history` holds the earlier turns).
`ItemOutput` (eval.schema) is what the pipeline returns: ranked chunk ids, answer segments with their
citations (half-open sentence ranges of a chunk), the abstention flag, the chunk ids the generator was given,
latency and cost. An exception from the pipeline is caught per item: the item is scored as an empty output
and the error is recorded (errors are never dropped from the denominators).

A run is built from a `Harness` (pipeline, the ingested chunks used for gold mapping, the judges' gateway and
the document sentence streams). The CLI loads one from `--pipeline module:setup`, where
`async def setup(config: str, *, doc_keys: Sequence[str], stream: bool = False) -> Harness`.

Flow, per run:

1. Select the live items (`audit_status: dropped` never runs) of one split (`eval.splits`: train / dev / test
   by `source_doc` hash) and optionally filter them (`--filter stratum=injection`).
2. Answering: every item goes through the pipeline; spend is checked before each item.
3. Scoring: gold sentence ranges (document sentence stream) are mapped to chunk sentences by text
   (`eval.mapping.map_quote`); the deterministic graders run; the judges (`eval.judge`) run concurrently
   through `gather_bounded`; the per-item `attribution` signals are recorded for `eval.attribution`.
4. `--freeze NAME` writes `eval/baselines/NAME.json` (`eval.baseline.freeze`); `--compare NAME` exits 1 when a
   metric regressed against it.

Suites: `retrieval` = retrieval metrics only (no judge); `full` = answers, deterministic graders and judges.
Metrics (per item, only where defined): `claim_completeness`, `answer_pass` (all required claims entailed; for
abstain items the abstention judge's verdict), `support_rate`, `abstention_correct` (strata unanswerable /
false_premise / versioned), `must_not_contain_ok`, `injection_violation` (injection stratum),
`citation_integrity`, `cite_sent_precision` / `cite_sent_recall` / `cite_chunk_*`, `recall@8`, `mrr@40`,
`latency_ms`, `ttft_ms`, `cost_usd`. Judges never see the system's model name (see `eval.judge`).

CLI (from backend/, PYTHONPATH=..):

    python -m eval.run --suite full --config answers --split dev --max-usd 1 \\
        --pipeline eval.pipeline_http:setup
    python -m eval.run --suite retrieval --config retrieval@2 --split train \\
        --pipeline eval.pipeline_retrieval:setup
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
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.ids import uuid7
from app.db.models.eval import EvalResult, EvalRun
from eval import baseline as bl
from eval import fingerprint
from eval.answers_dump import answer_row, answers_path, write_answers
from eval.graders import (
    RECALL_K,
    citation_metrics,
    citations_of,
    injection_violation,
    must_not_contain_ok,
    retrieval_metrics,
)
from eval.judge import (
    CONCURRENCY,
    PROMPTS,
    AbstainJudgement,
    ClaimJudgement,
    SegmentView,
    SpendCapReached,
    SupportJudgement,
    abstain_applicable,
    gather_bounded,
    judge_abstention,
    judge_claims,
    judge_support,
    overrules,
    segment_views,
    verdict_record,
)
from eval.loader import DATASET_ID
from eval.mapping import ChunkInfo, GoldTarget, map_quote
from eval.report import REPORTS_DIR, ItemResult, RunResult, write_report, write_sidecar
from eval.schema import EvalContext, GoldItem, ItemOutput, Query
from eval.splits import SPLITS, live_items, select_dropped, select_split, split_counts, split_of

SUITES = ("retrieval", "full")
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

Pipeline = Callable[[Query, EvalContext], Awaitable[ItemOutput]]
NewSession = Callable[[Query, EvalContext], Awaitable[uuid.UUID]]


@dataclass
class Harness:
    pipeline: Pipeline
    chunks_by_doc: Mapping[str, Sequence[ChunkInfo]]  # eval document key -> the ingested chunks
    ctx: EvalContext = field(default_factory=EvalContext)
    new_session: NewSession | None = None
    # total spend so far (USD) for `--max-usd`: pipeline answering + judges
    spent: Callable[[], Awaitable[float]] | None = None
    close: Callable[[], Awaitable[None]] | None = None  # release clients (HTTP) when the run ends
    judge_gw: Any = None  # the judges' gateway (answer suites)
    judge_guard: Any = None  # the judges' spend guard
    # document key -> document sentence texts (the stream gold_evidence ranges index; None = unavailable)
    gold_units: Callable[[str], Sequence[str] | None] | None = None
    # the answer-side config the server recorded for this run's turns (eval.fingerprint.merge_answer_config)
    answer_config: Callable[[], Awaitable[dict[str, Any] | None]] | None = None


# --- selection --------------------------------------------------------------------------------------------


def select_filter(items: Sequence[GoldItem], spec: str | None) -> list[GoldItem]:
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


def gold_targets(
    item: GoldItem,
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
    item: GoldItem,
    out: ItemOutput,
    targets: Sequence[GoldTarget],
    chunks: Mapping[str, ChunkInfo],
    *,
    answers: bool,
) -> dict[str, float]:
    mapped = [t for t in targets if t.mapped]
    m: dict[str, float] = {}
    if mapped:
        m.update(retrieval_metrics(mapped, out.retrieved))
    if not answers:
        return m
    m.update(citation_metrics(item, out, targets, chunks))
    if item.must_not_contain:
        m["must_not_contain_ok"] = must_not_contain_ok(item.must_not_contain, out)
    if item.stratum == "injection":
        m["injection_violation"] = injection_violation(item, out, chunks)
    m["abstained"] = float(out.abstained)
    return m


def attribution_signals(
    item: GoldItem,
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
    gold_known = bool(targets) and units_ok and not should_abstain
    sig: dict[str, Any] = {k: None for k in ATTR_KEYS}
    sig["should_abstain"] = should_abstain
    sig["abstained"] = out.abstained
    if gold_known:
        sig["gold_in_chunk"] = all(t.mapped for t in targets)
        if mapped:
            ret = set(out.retrieved)
            sig["gold_in_candidates"] = all(bool(t.chunk_ids & ret) for t in mapped)
            if out.evidence is not None:
                ev = set(out.evidence)
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
    abstain: AbstainJudgement | None = None
    errors: list[str] = field(default_factory=list)
    budget_stop: bool = False


async def judge_support_for(
    gw: Any, views: Sequence[SegmentView], question: str, documents: Sequence[str], *, guard: Any = None
) -> SupportJudgement:
    return await judge_support(gw, views, guard=guard, question=question, documents=documents)


async def _judge_all(
    pending: Sequence[tuple[GoldItem, ItemOutput, Mapping[str, ChunkInfo]]],
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
        views = segment_views(out, chunks)
        if any(s.passages for s in views):
            calls.append(
                call(judge_support_for, views, item.question, [item.source_doc] if item.source_doc else [])
            )
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
            r.abstain = val
            r.metrics["abstention_correct"] = float(val.correct)
    for r in results:
        if r.claims is not None:
            r.metrics["claim_completeness"] = r.claims.completeness
            r.metrics["answer_pass"] = float(r.claims.answer_pass)
        if r.support is not None and not math.isnan(r.support.rate):
            r.metrics["support_rate"] = r.support.rate
        if r.claims is not None or r.support is not None:
            r.metrics["judge_quote_overrules"] = float(overrules(r.claims, r.support))
    for (item, _out, _c), r in zip(pending, results, strict=True):
        if item.answer_type == "abstain" and "abstention_correct" in r.metrics:
            # no claims: the abstention judge's verdict is the pass
            r.metrics["answer_pass"] = r.metrics["abstention_correct"]
    return results


# --- answering --------------------------------------------------------------------------------------------


@dataclass
class Answered:
    item: GoldItem
    out: ItemOutput
    error: str | None
    cost_usd: float


async def _answer(item: GoldItem, h: Harness) -> Answered:
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
    items: Sequence[GoldItem], h: Harness, *, max_usd: float | None, concurrency: int
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
            if nxt >= len(items):  # another worker took the last item while this one awaited the ledger
                return
            i = nxt
            nxt += 1
            results[i] = await _answer(items[i], h)

    await asyncio.gather(*(worker() for _ in range(max(1, concurrency))))
    return [results[i] for i in sorted(results)], stopped


def _extra(out: ItemOutput) -> dict[str, float]:
    m: dict[str, float] = {}
    if out.latency_ms is not None:
        m["latency_ms"] = float(out.latency_ms)
    if out.ttft_ms is not None:
        m["ttft_ms"] = float(out.ttft_ms)
    if out.cost_usd is not None:
        m["cost_usd"] = float(out.cost_usd)
    return m


# --- the run ----------------------------------------------------------------------------------------------


async def run_suite(
    items: Sequence[GoldItem],
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
    split_items: Sequence[GoldItem] | None = None,
    filter_spec: str | None = None,
    dropped_only: bool = False,
) -> tuple[RunResult, pathlib.Path]:
    """Run `items` (deterministic order) and write the report, the sidecar and, for answer suites, the answers
    file. `dropped_only` runs the given `audit_status: dropped` items (never part of a normal run). The run
    says so: its split reads `<split>+dropped`, `config.dropped_only` is set and the fingerprint's dataset id
    differs, so it cannot be paired with, or mistaken for, a normal run."""
    if suite not in SUITES:
        raise ValueError(f"unknown suite {suite!r}")
    answers = suite != "retrieval"
    if answers and (harness.judge_gw is None):
        raise ValueError("answer suites need harness.judge_gw (the judges' gateway)")
    if dropped_only:
        split = f"{split}+dropped"
    else:
        items = live_items(items)
    gold = (split_items if dropped_only else live_items(split_items)) if split_items is not None else items
    run = RunResult(
        uuid7(),
        name,
        suite,
        split,
        git_sha or _git_sha(),
        dt.datetime.now(dt.UTC),
        config={"name": name, "suite": suite, "split": split, "n_items": len(items), "dataset": DATASET_ID},
    )
    if dropped_only:
        run.config["dropped_only"] = True
    if sm is not None:
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

    answer_rows: list[dict[str, Any]] = []
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
        top = set(a.out.retrieved[:RECALL_K])
        hits = [bool(t.chunk_ids & top) for t in targets if t.mapped] or None  # per gold range, for McNemar
        run.items.append(
            ItemResult(
                item.id,
                item.stratum,
                split_of(item.source_doc),
                m,
                err,
                quote_hits=hits,
                degraded=a.out.degraded,
                source_doc=item.source_doc,
                stratum=item.stratum,
                attribution=attr,
            )
        )
        if answers:
            failed = bool(attr["failed"]) if attr else bool(err)
            answer_rows.append(
                answer_row(
                    item, a.out, chunks, failed=failed, judged=verdict_record(j.claims, j.support, j.abstain)
                )
            )
    if stopped:
        run.config["stopped"] = "budget"
    if harness.spent is not None:
        run.config["spent_usd"] = round(await harness.spent(), 4)
    run.finished_at = dt.datetime.now(dt.UTC)
    run.fingerprint = fingerprint.build(
        dataset_id=DATASET_ID + (":dropped" if dropped_only else ""),
        split=split,
        split_items=gold,
        evaluated_ids=[i.item_id for i in run.items],
        filter_spec=filter_spec,
        judge=fingerprint.judge_versions(PROMPTS) if answers else {},
        answer=await harness.answer_config() if harness.answer_config is not None else None,
        code=fingerprint.code_version(),
    )
    if sm is not None:
        await _persist(sm, run)
    write_sidecar(run, report_dir)
    if answers:
        write_answers(answer_rows, answers_path(run.run_id, report_dir))
    return run, write_report(run, report_dir)


async def _persist(sm: async_sessionmaker[AsyncSession], run: RunResult) -> None:
    async with sm() as s:
        row = await s.get(EvalRun, run.run_id)
        assert row is not None
        row.finished_at = run.finished_at
        for it in run.items:
            for metric, value in sorted(it.metrics.items()):
                if not math.isfinite(value):
                    raise ValueError(f"non-finite metric {metric} for {it.item_id}")
                s.add(EvalResult(eval_run_id=run.run_id, question_id=it.item_id, metric=metric, value=value))
        await s.commit()


def degraded_abort(run: RunResult, max_rate: float) -> str | None:
    """The ABORT line when too many items ran in a degraded configuration, else None."""
    rate = run.degraded_rate()
    if rate > max_rate:
        kinds = run.degraded_by_kind()
        return f"ABORT: degraded_rate {rate:.3f} > {max_rate:.3f} ({kinds}); results are invalid"
    return None


def ttft_summary(run: RunResult) -> str | None:
    """`ttft_ms p50 <n> p95 <n> (n=<k>)` over the items that streamed a segment; None when none did."""
    vals = sorted(m["ttft_ms"] for i in run.items if "ttft_ms" in (m := i.metrics))
    if not vals:
        return None
    p50 = vals[(len(vals) - 1) // 2]
    p95 = vals[min(len(vals) - 1, math.ceil(0.95 * len(vals)) - 1)]
    return f"ttft_ms p50 {p50:.0f} p95 {p95:.0f} (n={len(vals)})"


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


# --- CLI --------------------------------------------------------------------------------------------------


def _load_setup(spec: str, *, stream: bool = False) -> Callable[..., Awaitable[Harness]]:
    mod, _, attr = spec.partition(":")
    if not attr:
        raise SystemExit("--pipeline must be module:callable")
    fn: Callable[..., Awaitable[Harness]] = getattr(importlib.import_module(mod), attr)
    if not stream:
        return fn
    if "stream" not in inspect.signature(fn).parameters:
        raise SystemExit(f"--stream: {spec} does not support streaming")
    return functools.partial(fn, stream=True)


async def _main(a: argparse.Namespace) -> int:
    from app.db.session import get_sessionmaker
    from eval.build.build_docs import item_docs, load_documents
    from eval.loader import load_dataset

    all_items = load_dataset().items
    if a.only_dropped and (a.freeze or a.compare):
        raise SystemExit("--only-dropped runs are never frozen or compared to a baseline")
    pick = select_dropped if a.only_dropped else select_split
    items = select_filter(pick(all_items, a.split), a.filter)
    kind = "dropped" if a.only_dropped else "live"
    print(f"{a.split}: {len(items)} {kind} items; splits (items, source_docs): {split_counts(all_items)}")
    docs = load_documents()
    keys = sorted({d for i in items for d in item_docs(i) if docs.get(d, {}).get("kind") != "virtual"})
    t_wall = time.monotonic()
    harness = await _load_setup(a.pipeline, stream=a.stream)(a.config, doc_keys=keys)
    sm = None if a.no_db else get_sessionmaker()
    try:
        run, path = await run_suite(
            items,
            harness,
            name=a.config,
            suite=a.suite,
            split=a.split,
            sm=sm,
            max_usd=a.max_usd,
            answer_concurrency=a.answer_concurrency,
            split_items=pick(all_items, a.split),
            filter_spec=a.filter,
            dropped_only=a.only_dropped,
        )
    finally:
        if harness.close is not None:
            await harness.close()
    print(f"wall_s {time.monotonic() - t_wall:.1f}")
    print(f"degraded_rate {run.degraded_rate():.3f} {run.degraded_by_kind()}")
    print(
        f"spent_usd {run.config.get('spent_usd')}" + (f" (cap {a.max_usd})" if a.max_usd is not None else "")
    )
    if a.stream:
        print(ttft_summary(run) or "ttft_ms: no streamed segment events")
    if run.config.get("stopped") == "budget":
        print(f"stopped: budget (cap ${a.max_usd}); partial run, nothing frozen")
        return 3
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
    p.add_argument("--pipeline", required=True, help="module:setup, async (config, *, doc_keys) -> Harness")
    p.add_argument("--split", choices=(*SPLITS, "all"), default="all")
    p.add_argument(
        "--answer-concurrency",
        type=int,
        default=2,
        help="items answered at once (the judges run in one bounded gather afterwards)",
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
    p.add_argument("--filter", default=None, help="only matching items, e.g. stratum=injection")
    p.add_argument(
        "--stream",
        action="store_true",
        help="answer over the SSE stream and report TTFT (p50/p95); needs a setup with a `stream` parameter",
    )
    p.add_argument(
        "--only-dropped",
        action="store_true",
        help="run the audit_status=dropped items of --split (stored gold as is) instead of the live ones; "
        "the report and fingerprint mark it, and it is never frozen or compared",
    )
    p.add_argument("--no-db", action="store_true", help="skip eval_runs/eval_results rows")
    return asyncio.run(_main(p.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
