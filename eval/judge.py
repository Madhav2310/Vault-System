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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict

from app.llm.errors import LlmError
from app.llm.gateway import LlmGateway, MemoryLedger, render_inputs
from app.llm.routing import EFFORT_SUPPORT, ROLES, ModelRouter, Role, Route
from eval.graders import grade_item
from eval.mapping import ChunkInfo, GoldTarget
from eval.schema import CitedRange, GoldenItem, ItemOutput

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
    p = gw.prompts.get(*PROMPTS[jt])
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


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="eval.judge")
    ap.add_argument(
        "--inter-judge", action="store_true", help="primary (deep) vs secondary (flagship) agreement"
    )
    ap.add_argument("--sample", type=int, default=20, help="items per judge type")
    ap.add_argument("--split", default="train", choices=["train", "test", "smoke", "all"])
    ap.add_argument("--max-usd", type=Decimal, default=None, help="spend cap (mandatory for live calls)")
    ap.add_argument("--dry-run", action="store_true", help="scripted FakeProvider replies, no network")
    a = ap.parse_args(argv)
    if not a.inter_judge:
        ap.error("nothing to do: pass --inter-judge")
    if a.sample < 1:
        ap.error("--sample must be >= 1")
    if not a.dry_run and a.max_usd is None:
        print("refusing live calls without an explicit --max-usd cap", file=sys.stderr)
        return 2
    ledger = MemoryLedger()
    gw = _dry_gateway(ledger) if a.dry_run else _live_gateway(ledger)
    guard = SpendGuard(ledger, a.max_usd if a.max_usd is not None else Decimal(1000))  # dry run: no spend
    try:
        check_tiers(gw.router)
        return asyncio.run(_inter_judge_cli(a.sample, a.split, gw, guard))
    except SpendCapReached as e:
        print(f"stopped: {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
