"""LLM judges for the golden set: three narrow judges, each a versioned prompt with a strict structured
verdict, run on role `deep` (calibration may run them on `flagship`).

- claims (`eval_judge_claims`): each required claim entailed by the answer? completeness = recall over the
  required claims; answer_pass = every required claim entailed.
- support (`eval_judge_support`): each answer segment entailed by its own cited sentences?
- abstain (`eval_judge_abstain`): did the answer decline, correct a false premise or pick the right version as
  the item requires (strata unanswerable / false_premise / versioned)?

Code applies the decisions. A "yes" that carries a quote the answer (or the cited passages) does not contain
is overruled to "no": the model's quote is never trusted.

Bias controls (the judge shares the generator's vendor): deterministic graders run first (eval.graders);
reference-guided (gold claims are part of the input); blind (the input is answer text, evidence and gold only,
and model names are scrubbed); judge tier >= generator tier (`check_tiers`; roles ordered fast < balanced <
deep < flagship); human labels for calibration (`--calibrate`).

CLI (from backend/, PYTHONPATH=..):
    python -m eval.judge --export-labels 200 --answers ../eval/reports/<run_id>.answers.jsonl
    python -m eval.judge --calibrate ../eval/labels/labeled.yaml --max-usd 3     # live: the cap is mandatory
Prints TPR / TNR / kappa per judge; exits 1 when a judge misses the targets, 2 on a usage error, 3 when the
spend cap stopped the run.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import pathlib
import re
import sys
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict

from app.llm.gateway import LlmGateway, MemoryLedger, render_inputs
from app.llm.routing import EFFORT_SUPPORT, ROLES, ModelRouter, Role, Route
from eval.labels.schema import LabeledPair, LabeledSegment, LabelFile
from eval.mapping import ChunkInfo
from eval.schema import CitedRange, GoldItem, ItemOutput

PURPOSE = "eval_judge"  # gateway node: routed to role `deep` (routing.DEFAULT_ROUTES)
SECONDARY_ROUTE = "flagship"
SECONDARY_ROUTE_ROW = Route("flagship", "low")
ROLE_RANK: dict[str, int] = {r: i for i, r in enumerate(ROLES)}  # ROLES is ordered fast..flagship


class JudgeOutputError(ValueError):
    """The model returned a schema-valid verdict that does not cover the judged units exactly."""


class SpendCapReached(Exception):
    """The next call could push spend past the cap; nothing was sent."""


class TierError(ValueError):
    """A judge role ranks below a generator role."""


class BlindnessError(ValueError):
    """A judge input still names a generator model."""


# --- judge input (answer + evidence + gold only: nothing that identifies the generator) ----------------


@dataclass(frozen=True)
class SegmentView:
    text: str
    passages: tuple[str, ...]


def _passage(ci: ChunkInfo, c: CitedRange) -> str | None:
    n = len(ci.sentences)
    if not 0 <= c.sentence_start < c.sentence_end <= n:
        return None
    return ci.text[ci.sentences[c.sentence_start][0] : ci.sentences[c.sentence_end - 1][1]].strip()


def segment_views(out: ItemOutput, chunks: Mapping[str, ChunkInfo]) -> tuple[SegmentView, ...]:
    """The judge's view of one answer: each segment with its cited passages (stored sentences, never
    model-written quotes). Invalid or unknown citations contribute no passage."""
    segs: list[SegmentView] = []
    for seg in out.segments:
        passages: list[str] = []
        for c in seg.citations:
            ci = chunks.get(c.chunk_id)
            text = _passage(ci, c) if ci is not None else None
            if text:
                passages.append(text)
        segs.append(SegmentView(seg.text, tuple(passages)))
    return tuple(segs)


def _scrub(obj: Any, pat: re.Pattern[str]) -> Any:
    if isinstance(obj, str):
        return pat.sub("[model]", obj)
    if isinstance(obj, list):
        return [_scrub(x, pat) for x in obj]
    if isinstance(obj, dict):
        return {k: _scrub(v, pat) for k, v in obj.items()}
    return obj


def _name_pattern(names: Sequence[str]) -> re.Pattern[str]:
    alts = [re.escape(n) for n in sorted(set(names), key=len, reverse=True) if n]
    alts += [r"gpt-\d[\w.\-]*", r"claude-[\w.\-]+"]
    return re.compile("|".join(alts), re.IGNORECASE)


def assert_blind(inputs: Mapping[str, Any], model_names: Sequence[str]) -> None:
    blob = json.dumps(inputs, ensure_ascii=False).lower()
    for n in model_names:
        if n and n.lower() in blob:
            raise BlindnessError("judge input names a generator model")


# --- tiers --------------------------------------------------------------------------------------------


def ensure_secondary_route(router: ModelRouter) -> None:
    """Add the `eval_judge:flagship` table row (role + effort, never a model name) when absent."""
    key = f"{PURPOSE}:{SECONDARY_ROUTE}"
    if key not in router.routes:
        model = router.role_models[SECONDARY_ROUTE_ROW.role]
        if SECONDARY_ROUTE_ROW.effort not in EFFORT_SUPPORT[model]:
            raise TierError(f"{model} does not support effort {SECONDARY_ROUTE_ROW.effort}")
        router.routes[key] = SECONDARY_ROUTE_ROW


def check_tiers(router: ModelRouter) -> tuple[Role, Role]:
    """(primary judge role, secondary judge role); raises TierError when either ranks below the highest
    role any `generate*` route uses."""
    ensure_secondary_route(router)
    gen = max(
        (r.role for k, r in router.routes.items() if k.split(":")[0] == "generate"), key=ROLE_RANK.__getitem__
    )
    primary = router.resolve(PURPOSE).role
    secondary = router.resolve(PURPOSE, SECONDARY_ROUTE).role
    for name, role in (("primary", primary), ("secondary", secondary)):
        if ROLE_RANK[role] < ROLE_RANK[gen]:
            raise TierError(f"{name} judge role {role!r} is below generator role {gen!r}")
    return primary, secondary


# --- spend cap ------------------------------------------------------------------------------------------


class SpendGuard:
    """Checks `spent (gateway ledger) + worst case of the next call <= cap` before every call."""

    def __init__(self, ledger: MemoryLedger, cap_usd: Decimal):
        self.ledger, self.cap = ledger, cap_usd

    @property
    def spent(self) -> Decimal:
        return sum((r.cost_usd for r in self.ledger.records), Decimal(0))

    def check(self, worst_case: Decimal) -> None:
        if self.spent + worst_case > self.cap:
            raise SpendCapReached(f"spent {self.spent} + next {worst_case} > cap {self.cap}")


def _worst_case_for(
    gw: LlmGateway, prompt: tuple[str, int], inputs: Mapping[str, Any], route: str | None
) -> Decimal:
    p = gw.prompts.get(*prompt)
    res = gw.router.resolve(PURPOSE, route, default=Route(p.role, p.effort))
    price = gw.prices.price(res.model)
    in_tok = (len(p.body) + len(render_inputs(inputs))) // 4 + 1
    return (Decimal(in_tok) * price.input + Decimal(p.max_tokens) * price.output) / Decimal(1_000_000)


# --- claim, support and abstention judges -----------------------------------------------------------------

PROMPTS: dict[str, tuple[str, int]] = {
    "claims": ("eval_judge_claims", 5),
    "support": ("eval_judge_support", 3),
    "abstain": ("eval_judge_abstain", 1),
}
ABSTAIN_STRATA = ("unanswerable", "false_premise", "versioned")
EXPECTED_BEHAVIOUR: dict[str, str] = {
    "unanswerable": "States that the documents do not contain the information and does not invent a value.",
    "false_premise": "Rejects or corrects the false premise instead of answering as if it held.",
    "versioned": "Answers for the document version or date the question depends on, without mixing versions.",
}
CONCURRENCY = 8


class ClaimDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claim_index: int
    entailed: bool
    quote: str


class ClaimsVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decisions: list[ClaimDecision]


class SegmentDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    segment_index: int
    supported: bool
    quote: str


class SupportVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decisions: list[SegmentDecision]


class AbstainVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str
    behaviour: Literal["declined", "corrected", "answered_as_asked", "other"]
    correct: bool


_PUNCT = str.maketrans(
    {"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u2013": "-", "\u2014": "-", "\u2212": "-"}
)


def _norm(s: str) -> str:
    """Casefold, collapse whitespace, fold curly quotes and dashes."""
    return " ".join(unicodedata.normalize("NFKC", s).translate(_PUNCT).casefold().split())


def _quote_in(quote: str, haystacks: Sequence[str]) -> bool:
    q = _norm(quote)
    return bool(q) and any(q in _norm(h) for h in haystacks)


async def _call(
    gw: LlmGateway,
    key: str,
    schema: type[BaseModel],
    inputs: dict[str, Any],
    *,
    route: str | None,
    guard: SpendGuard | None,
) -> tuple[Any, Decimal]:
    check_tiers(gw.router)
    names = list(gw.router.role_models.values())
    inputs = _scrub(inputs, _name_pattern(names))
    assert_blind(inputs, names)
    if guard is not None:
        guard.check(_worst_case_for(gw, PROMPTS[key], inputs, route))
    res = await gw.call(
        purpose=PURPOSE,
        lane="background",
        prompt=PROMPTS[key],
        owner_id=None,
        schema=schema,
        route=route,
        inputs=inputs,
    )
    if res.output is None:
        raise JudgeOutputError("no parsed output")
    return res.output, res.cost_usd


async def gather_bounded[T](coros: Sequence[Any], limit: int = CONCURRENCY) -> list[T]:
    """Run independent judge calls concurrently, at most `limit` at once; results keep the input order."""
    sem = asyncio.Semaphore(limit)

    async def one(c: Any) -> T:
        async with sem:
            return await c  # type: ignore[no-any-return]

    return list(await asyncio.gather(*(one(c) for c in coros)))


def _complete_indices(got: Sequence[int], n: int, what: str) -> None:
    if sorted(got) != list(range(n)):
        raise JudgeOutputError(f"{what} decisions {sorted(got)} do not cover 0..{n - 1} exactly once")


# claim judge


@dataclass(frozen=True)
class UnitVerdict:
    """One judged unit (claim or segment) as the model said it and as code applied it."""

    index: int
    decision: bool  # after the verbatim-quote check: what the metric uses
    raw: bool  # what the model said
    quote: str
    quote_verified: bool | None  # None: no quote was required or given
    overruled: bool  # raw True flipped to False by the quote check

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "decision": self.decision,
            "raw": self.raw,
            "quote": self.quote,
            "quote_verified": self.quote_verified,
            "overruled": self.overruled,
        }


@dataclass(frozen=True)
class ClaimJudgement:
    item_id: str
    entailed: tuple[bool, ...]  # per required claim, in item order
    cost_usd: Decimal
    details: tuple[UnitVerdict, ...] = ()

    @property
    def completeness(self) -> float:
        return sum(self.entailed) / len(self.entailed) if self.entailed else math.nan

    @property
    def answer_pass(self) -> bool:
        return bool(self.entailed) and all(self.entailed)


def claim_inputs(item: GoldItem, answer: str) -> dict[str, Any]:
    return {
        "question": item.question,
        "answer": answer,
        "claims": [
            {"index": i, "claim": c, "accepted_variants": list(item.alternates.get(c, []))}
            for i, c in enumerate(item.required_claims)
        ],
    }


async def judge_claims(
    gw: LlmGateway,
    item: GoldItem,
    answer: str,
    *,
    route: str | None = None,
    guard: SpendGuard | None = None,
) -> ClaimJudgement:
    n = len(item.required_claims)
    if n == 0:
        raise ValueError(f"item {item.id} has no required claims")
    out, cost = await _call(gw, "claims", ClaimsVerdict, claim_inputs(item, answer), route=route, guard=guard)
    _complete_indices([d.claim_index for d in out.decisions], n, "claim")
    by = {d.claim_index: d for d in out.decisions}
    details = []
    for i in range(n):
        d = by[i]
        verified = _quote_in(d.quote, [answer])
        ok = d.entailed and verified
        details.append(UnitVerdict(i, ok, d.entailed, d.quote, verified, d.entailed and not verified))
    return ClaimJudgement(item.id, tuple(v.decision for v in details), cost, tuple(details))


# support judge


@dataclass(frozen=True)
class SupportJudgement:
    supported: tuple[bool | None, ...]  # per segment; None = no cited passages, not judged
    cost_usd: Decimal
    details: tuple[UnitVerdict | None, ...] = ()  # per segment; None = not judged

    @property
    def rate(self) -> float:
        judged = [s for s in self.supported if s is not None]
        return sum(judged) / len(judged) if judged else math.nan


async def judge_support(
    gw: LlmGateway,
    segments: Sequence[SegmentView],
    *,
    route: str | None = None,
    guard: SpendGuard | None = None,
    question: str | None = None,
    documents: Sequence[str] = (),
) -> SupportJudgement:
    """`question` and `documents` (names of the source documents) are metadata for the prompt's naming rule;
    they are never evidence."""
    idx = [i for i, s in enumerate(segments) if s.passages]
    if not idx:
        return SupportJudgement(tuple(None for _ in segments), Decimal(0), tuple(None for _ in segments))
    inputs: dict[str, Any] = {}
    if question:
        inputs["question"] = question
    if documents:
        inputs["documents"] = list(documents)
    inputs["segments"] = [
        {"index": i, "text": segments[i].text, "cited_passages": list(segments[i].passages)} for i in idx
    ]
    out, cost = await _call(gw, "support", SupportVerdict, inputs, route=route, guard=guard)
    by = {d.segment_index: d for d in out.decisions}
    if len(out.decisions) != len(idx) or set(by) != set(idx):
        raise JudgeOutputError(f"segment decisions {sorted(by)} do not match the judged segments {idx}")
    res: list[bool | None] = [None] * len(segments)
    det: list[UnitVerdict | None] = [None] * len(segments)
    for i in idx:
        d = by[i]
        verified = _quote_in(d.quote, segments[i].passages) if d.quote else None
        ok = d.supported and verified is not False
        det[i] = UnitVerdict(i, ok, d.supported, d.quote, verified, d.supported and not ok)
        res[i] = ok
    return SupportJudgement(tuple(res), cost, tuple(det))


# abstention judge


@dataclass(frozen=True)
class AbstainJudgement:
    item_id: str
    correct: bool
    behaviour: str
    reason: str
    cost_usd: Decimal


def overrules(*js: ClaimJudgement | SupportJudgement | None) -> int:
    """Model 'yes' decisions flipped to 'no' by the verbatim-quote check (metric `judge_quote_overrules`)."""
    n = 0
    for j in js:
        if j is not None:
            n += sum(1 for v in j.details if v is not None and v.overruled)
    return n


def verdict_record(
    claims: ClaimJudgement | None, support: SupportJudgement | None, abstain: AbstainJudgement | None
) -> dict[str, Any]:
    """Per-claim / per-segment / abstention verdicts for the answers dump (additive `judge` field)."""
    rec: dict[str, Any] = {"prompts": {k: f"{n}@{v}" for k, (n, v) in PROMPTS.items()}}
    if claims is not None:
        rec["claims"] = [v.as_dict() for v in claims.details]
    if support is not None:
        rec["segments"] = [None if v is None else v.as_dict() for v in support.details]
    if abstain is not None:
        rec["abstain"] = {
            "behaviour": abstain.behaviour,
            "correct": abstain.correct,
            "reason": abstain.reason,
        }
    return rec


def abstain_applicable(item: GoldItem) -> bool:
    return item.stratum in ABSTAIN_STRATA


async def judge_abstention(
    gw: LlmGateway,
    item: GoldItem,
    answer: str,
    *,
    route: str | None = None,
    guard: SpendGuard | None = None,
) -> AbstainJudgement:
    if not abstain_applicable(item):
        raise ValueError(f"item {item.id} (stratum {item.stratum}) is not an abstention item")
    inputs = {
        "question": item.question,
        "answer": answer,
        "case": item.stratum,
        "expected_behaviour": EXPECTED_BEHAVIOUR[item.stratum],
        "reference_claims": list(item.required_claims),
    }
    out, cost = await _call(gw, "abstain", AbstainVerdict, inputs, route=route, guard=guard)
    # versioned: a correct answer answers from the current version, so `answered_as_asked` is expected;
    # elsewhere a contradictory verdict (correct but answered as asked) is not a pass
    ok = out.correct if item.stratum == "versioned" else out.correct and out.behaviour != "answered_as_asked"
    return AbstainJudgement(item.id, ok, out.behaviour, out.reason, cost)


# --- calibration math ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Confusion:
    """Judge vs human on a binary decision; positive = entailed / supported / correct."""

    tp: int = 0
    fn: int = 0
    tn: int = 0
    fp: int = 0

    @property
    def n(self) -> int:
        return self.tp + self.fn + self.tn + self.fp

    @property
    def tpr(self) -> float:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else math.nan

    @property
    def tnr(self) -> float:
        return self.tn / (self.tn + self.fp) if self.tn + self.fp else math.nan

    @property
    def kappa(self) -> float:
        return cohens_kappa(self)

    def __add__(self, o: Confusion) -> Confusion:
        return Confusion(self.tp + o.tp, self.fn + o.fn, self.tn + o.tn, self.fp + o.fp)


def confusion(pairs: Sequence[tuple[bool, bool]]) -> Confusion:
    """`pairs` are (human, judge)."""
    tp = sum(h and j for h, j in pairs)
    fn = sum(h and not j for h, j in pairs)
    tn = sum(not h and not j for h, j in pairs)
    fp = sum(not h and j for h, j in pairs)
    return Confusion(tp, fn, tn, fp)


def cohens_kappa(c: Confusion) -> float:
    """(po - pe) / (1 - pe); NaN when empty or when chance agreement is 1 (a constant rater pair)."""
    if c.n == 0:
        return math.nan
    po = (c.tp + c.tn) / c.n
    pe = ((c.tp + c.fn) * (c.tp + c.fp) + (c.fn + c.tn) * (c.fp + c.tn)) / (c.n * c.n)
    return math.nan if pe == 1 else (po - pe) / (1 - pe)


KAPPA_TARGET = 0.75
TNR_TARGET = 0.90


def meets_targets(c: Confusion) -> bool:
    return c.kappa >= KAPPA_TARGET and c.tnr >= TNR_TARGET  # NaN compares False


# --- label export -------------------------------------------------------------------------------------

SPLIT_FRACTIONS = (("few_shot", 0.15), ("dev", 0.40), ("test", 0.45))
FAILURE_SHARE = 0.6


def split_of_doc(source_doc: str, seed: int = 0) -> str:
    """Deterministic split by `source_doc`: sha256(seed:doc) as a fraction picks few_shot / dev / test."""
    f = int.from_bytes(hashlib.sha256(f"{seed}:{source_doc}".encode()).digest()[:8], "big") / 2**64
    edge = 0.0
    for name, share in SPLIT_FRACTIONS:
        edge += share
        if f < edge:
            return name
    return "test"


@dataclass(frozen=True)
class AnswerRecord:
    item_id: str
    answer: str
    segments: tuple[SegmentView, ...] = ()
    failed: bool = False


def load_answers(path: str) -> list[AnswerRecord]:
    """JSONL: {"item_id", "answer", "segments": [{"text", "passages": [...]}], "failed": bool}."""
    out: list[AnswerRecord] = []
    with open(path, encoding="utf-8") as f:
        for ln in f:
            if not ln.strip():
                continue
            r = json.loads(ln)
            segs = tuple(SegmentView(s["text"], tuple(s.get("passages", ()))) for s in r.get("segments", []))
            out.append(AnswerRecord(r["item_id"], r["answer"], segs, bool(r.get("failed", False))))
    return out


def _pair_id(rec: AnswerRecord) -> str:
    return f"{rec.item_id}:{hashlib.sha256(rec.answer.encode()).hexdigest()[:8]}"


def _rank(seed: int, key: str) -> str:
    return hashlib.sha256(f"{seed}:{key}".encode()).hexdigest()


def export_labels(
    items: Mapping[str, GoldItem], answers: Sequence[AnswerRecord], n: int, seed: int = 0
) -> LabelFile:
    """Pick up to `n` pairs, about FAILURE_SHARE of them failures (all failures when fewer), in a seeded
    hash order; every pair carries its source_doc split and null labels."""
    usable = [
        a
        for a in answers
        if a.item_id in items
        and items[a.item_id].audit_status != "dropped"
        and (items[a.item_id].required_claims or abstain_applicable(items[a.item_id]))
    ]
    uniq = list({_pair_id(a): a for a in usable}.values())
    fails = sorted((a for a in uniq if a.failed), key=lambda a: _rank(seed, _pair_id(a)))
    passes = sorted((a for a in uniq if not a.failed), key=lambda a: _rank(seed, _pair_id(a)))
    want_fail = min(len(fails), math.ceil(n * FAILURE_SHARE))
    take_pass = min(len(passes), n - want_fail)
    take_fail = min(len(fails), n - take_pass)  # top up with failures when passes run short
    chosen = fails[:take_fail] + passes[:take_pass]
    pairs: list[LabeledPair] = []
    for a in sorted(chosen, key=lambda a: _pair_id(a)):
        it = items[a.item_id]
        segs = [LabeledSegment(text=s.text, passages=list(s.passages)) for s in a.segments]
        pairs.append(
            LabeledPair(
                pair_id=_pair_id(a),
                item_id=it.id,
                stratum=it.stratum,
                source_doc=it.source_doc,
                split=split_of_doc(it.source_doc, seed),  # type: ignore[arg-type]
                failed=a.failed,
                question=it.question,
                answer=a.answer,
                segments=segs,
                required_claims=list(it.required_claims),
                claim_labels=[None] * len(it.required_claims),
                support_labels=[None] * len(segs),
                abstain_label=None,
            )
        )
    return LabelFile(seed=seed, pairs=pairs)


def dump_labels(lf: LabelFile, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(lf.model_dump(mode="json"), f, sort_keys=False, allow_unicode=True, width=100)


def load_labels(path: str) -> LabelFile:
    with open(path, encoding="utf-8") as f:
        return LabelFile.model_validate(yaml.safe_load(f) or {})


# --- calibration run ----------------------------------------------------------------------------------


async def calibrate(
    gw: LlmGateway,
    labels: LabelFile,
    items: Mapping[str, GoldItem],
    *,
    split: str = "test",
    route: str | None = None,
    guard: SpendGuard | None = None,
) -> dict[str, Confusion]:
    """Run each judge on the labeled pairs of `split` and compare with the human labels, per decision."""
    pairs = [p for p in labels.pairs if p.split == split and p.item_id in items]

    async def claims(p: LabeledPair) -> list[tuple[bool, bool]]:
        if not any(x is not None for x in p.claim_labels):
            return []
        j = await judge_claims(gw, items[p.item_id], p.answer, route=route, guard=guard)
        return [(h, e) for h, e in zip(p.claim_labels, j.entailed, strict=True) if h is not None]

    async def support(p: LabeledPair) -> list[tuple[bool, bool]]:
        if not any(x is not None for x in p.support_labels):
            return []
        segs = [SegmentView(s.text, tuple(s.passages)) for s in p.segments]
        j = await judge_support(gw, segs, route=route, guard=guard)
        return [(h, bool(s)) for h, s in zip(p.support_labels, j.supported, strict=True) if h is not None]

    async def abstain(p: LabeledPair) -> list[tuple[bool, bool]]:
        if p.abstain_label is None:
            return []
        j = await judge_abstention(gw, items[p.item_id], p.answer, route=route, guard=guard)
        return [(p.abstain_label, j.correct)]

    out: dict[str, Confusion] = {}
    for name, fn in (("claims", claims), ("support", support), ("abstain", abstain)):
        rows: list[list[tuple[bool, bool]]] = await gather_bounded([fn(p) for p in pairs])
        out[name] = confusion([r for rs in rows for r in rs])
    return out


def format_calibration(res: Mapping[str, Confusion], *, route: str | None) -> str:
    def f(x: float) -> str:
        return "n/a" if math.isnan(x) else f"{x:.3f}"

    lines = [f"{'judge':8s} {'n':>4s} {'TPR':>6s} {'TNR':>6s} {'kappa':>6s}  target (kappa>=0.75, TNR>=0.90)"]
    for k, c in res.items():
        lines.append(
            f"{k:8s} {c.n:4d} {f(c.tpr):>6s} {f(c.tnr):>6s} {f(c.kappa):>6s}  "
            f"{'OK' if meets_targets(c) else 'BELOW'}"
        )
    lines.append(
        f"judge role: {route or 'deep'} (same vendor as the generator: agreement with humans is the only "
        "external check; a cross-family judge needs the Anthropic adapter and a key)"
    )
    return "\n".join(lines)


# --- CLI ------------------------------------------------------------------------------------------------


def _load_key_from_env_files() -> None:
    """Make OPENAI_API_KEY visible when run from a worktree: read it from the nearest gitignored `.env` up the
    tree (the main checkout's). Only that variable is read, and its value is never printed."""
    if os.environ.get("OPENAI_API_KEY"):
        return
    root = pathlib.Path(__file__).resolve().parents[1]
    for base in (root, *root.parents[:4]):
        f = base / ".env"
        if not f.exists():
            continue
        for line in f.read_text().splitlines():
            k, sep, v = line.partition("=")
            if sep and k.strip() == "OPENAI_API_KEY" and v.strip():
                os.environ["OPENAI_API_KEY"] = v.strip().strip("'\"")
                return


def live_gateway(ledger: MemoryLedger) -> LlmGateway:
    """The judges' own gateway (in-memory ledger, so their spend is capped separately from the system's)."""
    _load_key_from_env_files()
    # the judges never touch the database or Redis; Settings only requires the variables to exist
    os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://unused/unused")
    os.environ.setdefault("REDIS_URL", "redis://unused")
    from app.core.settings import get_settings
    from app.llm.pricing import price_table_from_settings
    from app.llm.prompts import default_registry
    from app.llm.providers.openai_chat import OpenAIResponsesProvider

    s = get_settings()
    key = s.openai_api_key.get_secret_value() if s.openai_api_key else None
    if not key:
        raise SystemExit("OPENAI_API_KEY is not set (gitignored .env)")
    return LlmGateway(
        chat=OpenAIResponsesProvider(key, s.llm_timeout_s),
        prompts=default_registry(),
        prices=price_table_from_settings(s),
        ledger=ledger,
        settings=s,
    )


async def _calibrate_cli(path: str, split: str, route: str | None, gw: LlmGateway, guard: SpendGuard) -> int:
    from eval.loader import load_dataset

    items = load_dataset().by_id()
    res = await calibrate(gw, load_labels(path), items, split=split, route=route, guard=guard)
    print(format_calibration(res, route=route))
    print(f"spent ${guard.spent} of cap ${guard.cap}")
    return 0 if all(meets_targets(c) for c in res.values()) else 1


def _export_cli(n: int, answers: str, out: str, seed: int) -> int:
    from eval.loader import load_dataset

    lf = export_labels(load_dataset().by_id(), load_answers(answers), n, seed)
    dump_labels(lf, out)
    by_split = {s: sum(p.split == s for p in lf.pairs) for s, _ in SPLIT_FRACTIONS}
    print(
        f"wrote {len(lf.pairs)} pairs ({sum(p.failed for p in lf.pairs)} failures) to {out}; "
        f"splits {by_split}"
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="eval.judge")
    ap.add_argument("--max-usd", type=Decimal, default=None, help="spend cap (mandatory for --calibrate)")
    ap.add_argument("--export-labels", type=int, metavar="N", help="write N (item, answer) pairs to label")
    ap.add_argument("--answers", help="JSONL of answers for --export-labels (see eval/labels/README.md)")
    default_out = pathlib.Path(__file__).resolve().parent / "labels" / "labeled.yaml"
    ap.add_argument("--out", default=str(default_out), help="--export-labels output file")
    ap.add_argument("--seed", type=int, default=0, help="--export-labels seed (selection and split)")
    ap.add_argument("--calibrate", metavar="LABELED_YAML", help="TPR / TNR / kappa per judge vs human labels")
    ap.add_argument("--label-split", default="test", choices=["few_shot", "dev", "test"])
    ap.add_argument("--judge-route", default=None, choices=["flagship"], help="calibrate on role flagship")
    a = ap.parse_args(argv)
    if a.export_labels is not None:
        if a.export_labels < 1 or not a.answers:
            ap.error("--export-labels needs N >= 1 and --answers")
        return _export_cli(a.export_labels, a.answers, a.out, a.seed)
    if not a.calibrate:
        ap.error("nothing to do: pass --calibrate or --export-labels")
    if a.max_usd is None:
        print("refusing live calls without an explicit --max-usd cap", file=sys.stderr)
        return 2
    ledger = MemoryLedger()
    gw = live_gateway(ledger)
    guard = SpendGuard(ledger, a.max_usd)
    try:
        check_tiers(gw.router)
        return asyncio.run(_calibrate_cli(a.calibrate, a.label_split, a.judge_route, gw, guard))
    except SpendCapReached as e:
        print(f"stopped: {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
