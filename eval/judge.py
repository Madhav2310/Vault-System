"""LLM judges for Vault-Eval (plan S2.18, design section 18): four judge types, each a versioned prompt with a
strict structured verdict, run on role `deep` (primary) and, on a stable hash-selected subset, `flagship`
(secondary).

Bias controls (the judge shares the generator's vendor):
  1. deterministic graders run first (`deterministic_verdict`); an item they decide never reaches a judge;
  2. reference-guided: gold quotes / the expected answer are part of the judge input;
  3. blind: the input is built from answer text + evidence + gold only, and model names are scrubbed;
  4. judge tier >= generator tier (`check_tiers`; roles ordered fast < balanced < deep < flagship);
  5. inter-judge agreement (this module's CLI) until the human check (S10).

Verdict -> score: pass = 1.0, partial = 0.5, fail = 0.0. Two verdicts AGREE when their scores differ by at
most TOLERANCE (0.25, i.e. only identical verdicts agree: pass vs partial is a real disagreement). Items whose
primary and secondary verdicts disagree get `grader_suspect = True` and are left out of `tuner_scores`.

Sampling rule (`in_secondary_sample`): an item is on the secondary judge iff
sha256(f"{judge_type}:{item_id}") read as a fraction of 2**64 is below SECONDARY_FRACTION (0.25): stable
across runs and machines, independent of the item order.

CLI (from backend/, PYTHONPATH=..):
    python -m eval.judge --inter-judge --sample 20 --max-usd 6        # live: the cap is mandatory
    python -m eval.judge --inter-judge --sample 20 --dry-run          # scripted provider, no network
Prints per-type agreement; exits 1 when any type is below 0.80 (or has no cases), 2 on a usage error.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import re
import sys
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal, get_args

import yaml
from pydantic import BaseModel, ConfigDict

from app.llm.errors import LlmError
from app.llm.gateway import LlmGateway, MemoryLedger, render_inputs
from app.llm.routing import EFFORT_SUPPORT, ROLES, ModelRouter, Role, Route
from eval.graders import grade_item
from eval.labels.schema import LabeledPair, LabeledSegment, LabelFile
from eval.mapping import ChunkInfo, GoldTarget
from eval.schema import CitedRange, GoldenItem, GoldItemV2, ItemOutput

JudgeType = Literal["faithfulness", "citation", "completeness", "rubric"]
JUDGE_TYPES: tuple[JudgeType, ...] = get_args(JudgeType)
Verdict = Literal["pass", "partial", "fail"]

PURPOSE = "eval_judge"  # gateway node: routed to role `deep` (routing.DEFAULT_ROUTES)
SECONDARY_ROUTE = "flagship"
SECONDARY_ROUTE_ROW = Route("flagship", "low")
VERDICT_SCORE: dict[str, float] = {"pass": 1.0, "partial": 0.5, "fail": 0.0}
TOLERANCE = 0.25
SECONDARY_FRACTION = 0.25
AGREEMENT_TARGET = 0.80
ROLE_RANK: dict[str, int] = {r: i for i, r in enumerate(ROLES)}  # ROLES is ordered fast..flagship

# --- verdict schemas (strict-mode compatible: all fields required, no defaults; field order = generation)


class FaithfulnessVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str
    verdict: Verdict
    unsupported_claims: list[str]
    confidence: float


class CitationVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str
    verdict: Verdict
    unsupported_segments: list[int]
    confidence: float


class CompletenessVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str
    verdict: Verdict
    missing_facts: list[str]
    confidence: float


class RubricVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str
    verdict: Verdict
    criteria_met: list[bool]
    confidence: float


JudgeSchema = FaithfulnessVerdict | CitationVerdict | CompletenessVerdict | RubricVerdict
SCHEMAS: dict[JudgeType, type[JudgeSchema]] = {
    "faithfulness": FaithfulnessVerdict,
    "citation": CitationVerdict,
    "completeness": CompletenessVerdict,
    "rubric": RubricVerdict,
}
PROMPTS: dict[JudgeType, tuple[str, int]] = {t: (f"eval_judge_{t}", 1) for t in JUDGE_TYPES}


class JudgeOutputError(ValueError):
    """The model returned a schema-valid verdict that contradicts itself (rubric verdict vs criteria)."""


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


@dataclass(frozen=True)
class JudgeCase:
    item_id: str
    question: str
    segments: tuple[SegmentView, ...]
    reference_quotes: tuple[str, ...]
    expected_answer: str | tuple[str, ...] | None
    criteria: tuple[str, ...]

    @property
    def answer(self) -> str:
        return "\n".join(s.text for s in self.segments)

    @property
    def evidence(self) -> tuple[str, ...]:
        seen: dict[str, None] = {}
        for s in self.segments:
            for p in s.passages:
                seen[p] = None
        return tuple(seen)


def _passage(ci: ChunkInfo, c: CitedRange) -> str | None:
    n = len(ci.sentences)
    if not 0 <= c.sentence_start < c.sentence_end <= n:
        return None
    return ci.text[ci.sentences[c.sentence_start][0] : ci.sentences[c.sentence_end - 1][1]].strip()


def build_case(item: GoldenItem, out: ItemOutput, chunks: Mapping[str, ChunkInfo]) -> JudgeCase:
    """The judge's view of one graded answer. Invalid or unknown citations contribute no passage."""
    segs: list[SegmentView] = []
    for seg in out.segments:
        passages: list[str] = []
        for c in seg.citations:
            ci = chunks.get(c.chunk_id)
            text = _passage(ci, c) if ci is not None else None
            if text:
                passages.append(text)
        segs.append(SegmentView(seg.text, tuple(passages)))
    exp = item.expected_answer
    return JudgeCase(
        item_id=item.id,
        question=item.question or "",
        segments=tuple(segs),
        reference_quotes=tuple(g.quote for g in item.gold),
        expected_answer=tuple(exp) if isinstance(exp, list) else exp,
        criteria=tuple(item.rubric or ()),
    )


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


def build_inputs(case: JudgeCase, jt: JudgeType, *, model_names: Sequence[str] = ()) -> dict[str, Any]:
    """The `inputs` object for one judge call, with every model name scrubbed from the text."""
    q = case.question
    exp: Any = list(case.expected_answer) if isinstance(case.expected_answer, tuple) else case.expected_answer
    raw: dict[str, Any]
    if jt == "faithfulness":
        raw = {
            "question": q,
            "answer": case.answer,
            "evidence": [{"id": f"E{i}", "text": t} for i, t in enumerate(case.evidence, 1)],
            "reference_quotes": list(case.reference_quotes),
        }
    elif jt == "citation":
        raw = {
            "question": q,
            "segments": [
                {"id": i, "text": s.text, "cited_passages": list(s.passages)}
                for i, s in enumerate(case.segments)
            ],
            "reference_quotes": list(case.reference_quotes),
        }
    elif jt == "completeness":
        raw = {
            "question": q,
            "answer": case.answer,
            "expected_answer": exp,
            "reference_quotes": list(case.reference_quotes),
        }
    else:
        raw = {
            "question": q,
            "answer": case.answer,
            "criteria": [{"id": i, "text": c} for i, c in enumerate(case.criteria)],
            "reference_quotes": list(case.reference_quotes),
        }
    out: dict[str, Any] = _scrub(raw, _name_pattern(model_names))
    assert_blind(out, model_names)
    return out


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


# --- deterministic graders first ----------------------------------------------------------------------


@dataclass(frozen=True)
class DetVerdict:
    verdict: Verdict
    code: str


def applicable(item: GoldenItem, jt: JudgeType) -> bool:
    if item.category == "multi_turn":
        return False  # judged per turn through `GoldenItem.turn_item`
    if jt == "rubric":
        return bool(item.rubric)
    return item.grading not in ("rubric", "exact_meta", "set_meta", "abstention")


def deterministic_verdict(
    item: GoldenItem,
    out: ItemOutput,
    targets: Sequence[GoldTarget],
    chunks: Mapping[str, ChunkInfo],
    jt: JudgeType,
) -> DetVerdict | None:
    """A verdict from the deterministic graders (abstention, must_not_contain, exact/set meta, gold-citation
    overlap), or None when an LLM judge must fill the gap."""
    m = grade_item(item, out, targets, chunks, retrieval=False)
    if m.get("abstain_correct") == 0.0:
        return DetVerdict("fail", "ABSTENTION_MISMATCH")
    if item.expected_coverage == "insufficient":
        return DetVerdict("pass", "ABSTAINED_CORRECTLY")
    if m.get("must_not_contain_ok") == 0.0:
        return DetVerdict("fail", "FORBIDDEN_STRING")
    if "meta_correct" in m:
        return DetVerdict("pass" if m["meta_correct"] == 1.0 else "fail", "META_EXACT")
    if jt == "citation" and "cite_sent_recall" in m:
        rec, prec = m["cite_sent_recall"], m["cite_sent_precision"]
        if rec == 1.0 and prec == 1.0:
            return DetVerdict("pass", "GOLD_CITATION_OVERLAP")
        if rec == 0.0 and prec == 0.0:
            return DetVerdict("fail", "GOLD_CITATION_NO_OVERLAP")
    return None


# --- sampling and agreement ---------------------------------------------------------------------------


def _frac(key: str) -> float:
    return int(hashlib.sha256(key.encode()).hexdigest()[:16], 16) / 2**64


def in_secondary_sample(item_id: str, jt: JudgeType, fraction: float = SECONDARY_FRACTION) -> bool:
    return _frac(f"{jt}:{item_id}") < fraction


def sample_ids(ids: Sequence[str], n: int, salt: str = "inter-judge") -> list[str]:
    """The first `n` ids in a stable hash order (independent of input order)."""
    return sorted(ids, key=lambda i: _frac(f"{salt}:{i}"))[:n]


def verdicts_agree(a: str, b: str, tolerance: float = TOLERANCE) -> bool:
    return abs(VERDICT_SCORE[a] - VERDICT_SCORE[b]) <= tolerance


def agreement(pairs: Sequence[tuple[str | None, str | None]], tolerance: float = TOLERANCE) -> float:
    """Fraction of (primary, secondary) verdict pairs that agree; a missing verdict is a disagreement; NaN
    when there are no pairs."""
    if not pairs:
        return math.nan
    ok = sum(1 for a, b in pairs if a is not None and b is not None and verdicts_agree(a, b, tolerance))
    return ok / len(pairs)


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


def _worst_case(gw: LlmGateway, jt: JudgeType, inputs: Mapping[str, Any], route: str | None) -> Decimal:
    return _worst_case_for(gw, PROMPTS[jt], inputs, route)


def _worst_case_for(
    gw: LlmGateway, prompt: tuple[str, int], inputs: Mapping[str, Any], route: str | None
) -> Decimal:
    p = gw.prompts.get(*prompt)
    res = gw.router.resolve(PURPOSE, route, default=Route(p.role, p.effort))
    price = gw.prices.price(res.model)
    in_tok = (len(p.body) + len(render_inputs(inputs))) // 4 + 1
    return (Decimal(in_tok) * price.input + Decimal(p.max_tokens) * price.output) / Decimal(1_000_000)


# --- judging ------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class JudgeVerdict:
    judge_type: JudgeType
    tier: Literal["primary", "secondary"]
    role: str | None
    verdict: Verdict
    score: float
    confidence: float
    reason: str
    cost_usd: Decimal


def effective_verdict(out: JudgeSchema, n_criteria: int) -> Verdict:
    """The model's verdict; a rubric verdict is derived from `criteria_met` (code applies decisions)."""
    if isinstance(out, RubricVerdict):
        if len(out.criteria_met) != n_criteria:
            raise JudgeOutputError("criteria_met length does not match the criteria")
        met = sum(out.criteria_met)
        return "pass" if met == n_criteria else ("fail" if met == 0 else "partial")
    return out.verdict


async def run_judge(
    gw: LlmGateway,
    case: JudgeCase,
    jt: JudgeType,
    *,
    secondary: bool = False,
    guard: SpendGuard | None = None,
) -> JudgeVerdict:
    check_tiers(gw.router)
    inputs = build_inputs(case, jt, model_names=list(gw.router.role_models.values()))
    route = SECONDARY_ROUTE if secondary else None
    if guard is not None:
        guard.check(_worst_case(gw, jt, inputs, route))
    res = await gw.call(
        purpose=PURPOSE,
        lane="background",
        prompt=PROMPTS[jt],
        owner_id=None,
        schema=SCHEMAS[jt],
        route=route,
        inputs=inputs,
    )
    if res.output is None:
        raise JudgeOutputError("no parsed output")
    verdict = effective_verdict(res.output, len(case.criteria))
    return JudgeVerdict(
        jt,
        "secondary" if secondary else "primary",
        res.role,
        verdict,
        VERDICT_SCORE[verdict],
        min(1.0, max(0.0, res.output.confidence)),
        res.output.reason,
        res.cost_usd,
    )


@dataclass(frozen=True)
class Judgement:
    item_id: str
    judge_type: JudgeType
    source: Literal["deterministic", "judge", "error", "not_applicable"]
    verdict: Verdict | None
    score: float | None
    code: str | None  # deterministic reason code or error type
    primary: JudgeVerdict | None = None
    secondary: JudgeVerdict | None = None
    grader_suspect: bool = False


def mark_suspect(j: Judgement, tolerance: float = TOLERANCE) -> Judgement:
    """`grader_suspect` iff both judges ran and their verdicts disagree."""
    if j.primary is None or j.secondary is None:
        return j
    bad = not verdicts_agree(j.primary.verdict, j.secondary.verdict, tolerance)
    return Judgement(**{**j.__dict__, "grader_suspect": bad})


def tuner_scores(judgements: Sequence[Judgement]) -> dict[tuple[str, str], float]:
    """(item_id, judge_type) -> score for the tuner's accept/reject: scored items only, suspects left out."""
    return {
        (j.item_id, j.judge_type): j.score
        for j in judgements
        if j.score is not None and not j.grader_suspect and j.source in ("deterministic", "judge")
    }


async def judge_item(
    gw: LlmGateway,
    item: GoldenItem,
    out: ItemOutput,
    targets: Sequence[GoldTarget],
    chunks: Mapping[str, ChunkInfo],
    jt: JudgeType,
    *,
    secondary: bool | None = None,
    guard: SpendGuard | None = None,
    tolerance: float = TOLERANCE,
) -> Judgement:
    if not applicable(item, jt) and jt == "rubric":
        return Judgement(item.id, jt, "not_applicable", None, None, None)
    det = deterministic_verdict(item, out, targets, chunks, jt)
    if det is not None:
        return Judgement(item.id, jt, "deterministic", det.verdict, VERDICT_SCORE[det.verdict], det.code)
    if not applicable(item, jt):
        return Judgement(item.id, jt, "not_applicable", None, None, None)
    case = build_case(item, out, chunks)
    want_secondary = in_secondary_sample(item.id, jt) if secondary is None else secondary
    try:
        primary = await run_judge(gw, case, jt, guard=guard)
        sec = await run_judge(gw, case, jt, secondary=True, guard=guard) if want_secondary else None
    except LlmError as e:
        return Judgement(item.id, jt, "error", None, None, e.error_type)
    except JudgeOutputError:
        return Judgement(item.id, jt, "error", None, None, "JUDGE_OUTPUT_INCONSISTENT")
    j = Judgement(item.id, jt, "judge", primary.verdict, primary.score, None, primary, sec)
    return mark_suspect(j, tolerance)


# --- inter-judge agreement ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AgreementReport:
    judge_type: JudgeType
    n: int
    rate: float
    disagreeing: tuple[str, ...]
    errors: int


async def inter_judge(
    gw: LlmGateway,
    cases: Mapping[JudgeType, Sequence[JudgeCase]],
    *,
    guard: SpendGuard | None = None,
    tolerance: float = TOLERANCE,
) -> dict[JudgeType, AgreementReport]:
    """Run primary and secondary on every case; a failed call counts as a disagreement (conservative)."""
    reports: dict[JudgeType, AgreementReport] = {}
    for jt, cs in cases.items():
        pairs: list[tuple[str | None, str | None]] = []
        bad: list[str] = []
        errors = 0
        for c in cs:
            try:
                p = await run_judge(gw, c, jt, guard=guard)
                s = await run_judge(gw, c, jt, secondary=True, guard=guard)
                pair: tuple[str | None, str | None] = (p.verdict, s.verdict)
            except (LlmError, JudgeOutputError):
                errors += 1
                pair = (None, None)
            pairs.append(pair)
            if pair[0] is None or pair[1] is None or not verdicts_agree(pair[0], pair[1], tolerance):
                bad.append(c.item_id)
        reports[jt] = AgreementReport(jt, len(cs), agreement(pairs, tolerance), tuple(bad), errors)
    return reports


def _answer_text(item: GoldenItem) -> str:
    exp = item.expected_answer
    return "\n".join(exp) if isinstance(exp, list) else (exp or "")


def sample_cases(items: Sequence[GoldenItem], jt: JudgeType, n: int) -> list[JudgeCase]:
    """Cases for the agreement run: the dataset's own expected answer (a positive) or another item's expected
    answer against this item's evidence (a negative), so the judges see both verdict directions. Evidence is
    the gold quotes. Rubric items give both variants (there are few); other types alternate by hash rank."""
    pool = [i for i in items if applicable(i, jt) and i.expected_coverage != "insufficient"]
    if jt != "rubric":
        pool = [i for i in pool if i.gold]
    chosen_ids = sample_ids([i.id for i in pool], n)
    by_id = {i.id: i for i in pool}
    chosen = [by_id[i] for i in chosen_ids]
    cases: list[JudgeCase] = []
    for k, it in enumerate(chosen):
        variants = (True, False) if jt == "rubric" else ((k % 2 == 0),)
        for positive in variants:
            other = chosen[(k + 1) % len(chosen)] if len(chosen) > 1 else None
            if not positive and (other is None or other.id == it.id):
                continue
            text = _answer_text(it) if positive else _answer_text(other or it)
            passages = tuple(g.quote for g in it.gold)
            cases.append(
                JudgeCase(
                    item_id=f"{it.id}{'' if positive else '#neg'}",
                    question=it.question or "",
                    segments=(SegmentView(text, passages),),
                    reference_quotes=passages,
                    expected_answer=tuple(it.expected_answer)
                    if isinstance(it.expected_answer, list)
                    else it.expected_answer,
                    criteria=tuple(it.rubric or ()),
                )
            )
    return cases


# --- v2 claim-level judges (S21.06) -------------------------------------------------------------------
#
# Three narrow judges, each a binary decision per unit: claim entailed by the answer (completeness), segment
# entailed by its own cited sentences (support), answer behaved correctly on an unanswerable / false-premise /
# versioned item (abstention). Code applies the decisions: completeness = recall over required claims,
# answer_pass = all required claims entailed. A "supported" decision that carries a quote the answer (or the
# cited passages) does not contain is overruled to false: the model's quote is never trusted.
# The v1 judges above are untouched and still serve v1 reports.

PROMPTS_V2: dict[str, tuple[str, int]] = {
    "claims": (
        "eval_judge_claims",
        3,
    ),  # @3 compares numbers as quantities, accepts value lists and stated aliases; @1/@2 stay for old reports
    "support": ("eval_judge_support", 2),
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


async def _call_v2(
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
        guard.check(_worst_case_for(gw, PROMPTS_V2[key], inputs, route))
    res = await gw.call(
        purpose=PURPOSE,
        lane="background",
        prompt=PROMPTS_V2[key],
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
class Verdict2:
    """One judged unit (claim or segment) as the model said it and as code applied it (S21.06b)."""

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
    details: tuple[Verdict2, ...] = ()

    @property
    def completeness(self) -> float:
        return sum(self.entailed) / len(self.entailed) if self.entailed else math.nan

    @property
    def answer_pass(self) -> bool:
        return bool(self.entailed) and all(self.entailed)


def claim_inputs(item: GoldItemV2, answer: str) -> dict[str, Any]:
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
    item: GoldItemV2,
    answer: str,
    *,
    route: str | None = None,
    guard: SpendGuard | None = None,
) -> ClaimJudgement:
    n = len(item.required_claims)
    if n == 0:
        raise ValueError(f"item {item.id} has no required claims")
    out, cost = await _call_v2(
        gw, "claims", ClaimsVerdict, claim_inputs(item, answer), route=route, guard=guard
    )
    _complete_indices([d.claim_index for d in out.decisions], n, "claim")
    by = {d.claim_index: d for d in out.decisions}
    details = []
    for i in range(n):
        d = by[i]
        verified = _quote_in(d.quote, [answer])
        details.append(
            Verdict2(i, d.entailed and verified, d.entailed, d.quote, verified, d.entailed and not verified)
        )
    return ClaimJudgement(item.id, tuple(v.decision for v in details), cost, tuple(details))


# support judge


@dataclass(frozen=True)
class SupportJudgement:
    supported: tuple[bool | None, ...]  # per segment; None = no cited passages, not judged
    cost_usd: Decimal
    details: tuple[Verdict2 | None, ...] = ()  # per segment; None = not judged

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
    """`question` and `documents` (names of the source documents) are metadata for the naming rule of
    eval_judge_support@2; they are never evidence."""
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
    out, cost = await _call_v2(gw, "support", SupportVerdict, inputs, route=route, guard=guard)
    by = {d.segment_index: d for d in out.decisions}
    if len(out.decisions) != len(idx) or set(by) != set(idx):
        raise JudgeOutputError(f"segment decisions {sorted(by)} do not match the judged segments {idx}")
    res: list[bool | None] = [None] * len(segments)
    det: list[Verdict2 | None] = [None] * len(segments)
    for i in idx:
        d = by[i]
        verified = _quote_in(d.quote, segments[i].passages) if d.quote else None
        ok = d.supported and verified is not False
        det[i] = Verdict2(i, ok, d.supported, d.quote, verified, d.supported and not ok)
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
    rec: dict[str, Any] = {"prompts": {k: f"{n}@{v}" for k, (n, v) in PROMPTS_V2.items()}}
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


def abstain_applicable(item: GoldItemV2) -> bool:
    return item.stratum in ABSTAIN_STRATA


async def judge_abstention(
    gw: LlmGateway,
    item: GoldItemV2,
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
    out, cost = await _call_v2(gw, "abstain", AbstainVerdict, inputs, route=route, guard=guard)
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
    items: Mapping[str, GoldItemV2], answers: Sequence[AnswerRecord], n: int, seed: int = 0
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
    items: Mapping[str, GoldItemV2],
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


def _dry_gateway(ledger: MemoryLedger) -> LlmGateway:
    from app.core.settings import Settings
    from app.llm.pricing import DEFAULT_PRICES, PriceTable
    from app.llm.prompts import default_registry
    from app.llm.providers.fake import FakeProvider
    from app.llm.types import ChatRequest, ChatResponse, TextPart, Usage

    class DryProvider(FakeProvider):
        """Valid all-pass verdicts for whichever judge schema the request carries; no network."""

        async def complete(self, req: ChatRequest) -> ChatResponse:
            self.requests.append(req)
            props = (req.output_schema or {}).get("properties", {})
            body: dict[str, Any] = {"reason": "dry run", "verdict": "pass", "confidence": 0.9}
            if "unsupported_claims" in props:
                body["unsupported_claims"] = []
            if "unsupported_segments" in props:
                body["unsupported_segments"] = []
            if "missing_facts" in props:
                body["missing_facts"] = []
            if "criteria_met" in props:
                data = json.loads(
                    str(req.messages[0]["content"]).removeprefix("<inputs>").removesuffix("</inputs>")
                )
                body["criteria_met"] = [True] * len(data["criteria"])
            return ChatResponse((TextPart(json.dumps(body)),), "end_turn", Usage(1500, 150), None, req.model)

    settings = Settings(_env_file=None, database_url="postgresql+asyncpg://x/y", redis_url="redis://x")  # type: ignore[call-arg]
    return LlmGateway(
        chat=DryProvider(),
        prompts=default_registry(),
        prices=PriceTable({**DEFAULT_PRICES}),
        ledger=ledger,
        settings=settings,
    )


def _live_gateway(ledger: MemoryLedger) -> LlmGateway:
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


async def _inter_judge_cli(sample: int, split: str, gw: LlmGateway, guard: SpendGuard) -> int:
    from eval.loader import load_dataset

    items = load_dataset().select(split)
    cases = {jt: sample_cases(items, jt, sample) for jt in JUDGE_TYPES}
    reports = await inter_judge(gw, cases, guard=guard)
    worst = True
    for jt, r in reports.items():
        ok = r.n > 0 and r.rate >= AGREEMENT_TARGET
        worst = worst and ok
        rate = "n/a" if math.isnan(r.rate) else f"{r.rate:.2f}"
        print(f"{jt:13s} n={r.n:3d} agreement={rate} errors={r.errors} {'OK' if ok else 'BELOW 0.80'}")
        if r.disagreeing:
            print(f"  disagreeing: {', '.join(r.disagreeing)}")
    print(f"spent ${guard.spent} of cap ${guard.cap}")
    return 0 if worst else 1


async def _calibrate_cli(path: str, split: str, route: str | None, gw: LlmGateway, guard: SpendGuard) -> int:
    from eval.loader import load_dataset_v2

    items = load_dataset_v2().by_id()
    res = await calibrate(gw, load_labels(path), items, split=split, route=route, guard=guard)
    print(format_calibration(res, route=route))
    print(f"spent ${guard.spent} of cap ${guard.cap}")
    return 0 if all(meets_targets(c) for c in res.values()) else 1


def _export_cli(n: int, answers: str, out: str, seed: int) -> int:
    from eval.loader import load_dataset_v2

    lf = export_labels(load_dataset_v2().by_id(), load_answers(answers), n, seed)
    dump_labels(lf, out)
    by_split = {s: sum(p.split == s for p in lf.pairs) for s, _ in SPLIT_FRACTIONS}
    print(
        f"wrote {len(lf.pairs)} pairs ({sum(p.failed for p in lf.pairs)} failures) to {out}; "
        f"splits {by_split}"
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="eval.judge")
    ap.add_argument(
        "--inter-judge", action="store_true", help="primary (deep) vs secondary (flagship) agreement"
    )
    ap.add_argument("--sample", type=int, default=20, help="items per judge type")
    ap.add_argument("--split", default="train", choices=["train", "test", "smoke", "all"])
    ap.add_argument("--max-usd", type=Decimal, default=None, help="spend cap (mandatory for live calls)")
    ap.add_argument("--dry-run", action="store_true", help="scripted FakeProvider replies, no network")
    ap.add_argument("--export-labels", type=int, metavar="N", help="write N (item, answer) pairs to label")
    ap.add_argument("--answers", help="JSONL of answers for --export-labels (see eval/labels/README.md)")
    ap.add_argument("--out", default="eval/labels/labeled.yaml", help="--export-labels output file")
    ap.add_argument("--seed", type=int, default=0, help="--export-labels seed (selection and split)")
    ap.add_argument("--calibrate", metavar="LABELED_YAML", help="TPR / TNR / kappa per judge vs human labels")
    ap.add_argument("--label-split", default="test", choices=["few_shot", "dev", "test"])
    ap.add_argument("--judge-route", default=None, choices=["flagship"], help="calibrate on role flagship")
    a = ap.parse_args(argv)
    if a.export_labels is not None:
        if a.export_labels < 1 or not a.answers:
            ap.error("--export-labels needs N >= 1 and --answers")
        return _export_cli(a.export_labels, a.answers, a.out, a.seed)
    if not a.inter_judge and not a.calibrate:
        ap.error("nothing to do: pass --inter-judge, --calibrate or --export-labels")
    if a.sample < 1:
        ap.error("--sample must be >= 1")
    if not a.dry_run and a.max_usd is None:
        print("refusing live calls without an explicit --max-usd cap", file=sys.stderr)
        return 2
    if a.calibrate and a.dry_run:
        ap.error("--calibrate has no dry run: it compares live judge output with human labels")
    ledger = MemoryLedger()
    gw = _dry_gateway(ledger) if a.dry_run else _live_gateway(ledger)
    guard = SpendGuard(ledger, a.max_usd if a.max_usd is not None else Decimal(1000))  # dry run: no spend
    try:
        check_tiers(gw.router)
        if a.calibrate:
            return asyncio.run(_calibrate_cli(a.calibrate, a.label_split, a.judge_route, gw, guard))
        return asyncio.run(_inter_judge_cli(a.sample, a.split, gw, guard))
    except SpendCapReached as e:
        print(f"stopped: {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
