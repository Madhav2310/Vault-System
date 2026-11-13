"""Calibration of the support check (plan S17.09 AC7): `support_check@1` (role fast) against the
`eval_judge_faithfulness@1` judge (role deep) on the same segment and the same cited text.

Both verdicts are binarized: supported vs not (fast: `supported`; judge: `pass`). Reports the raw agreement and
Cohen's kappa. Cases are the dataset's own segments (`eval.judge.sample_cases`): half positives (the item's
expected answer against its own gold passages) and half negatives (another item's answer against this item's
passages), with real third-party passages. The judge is shown ONLY the segment text and its passages (no gold
quotes beyond them), so both graders see identical information.

CLI (from backend/, PYTHONPATH=..):
    python -m eval.support_calibration --n 40 --max-usd 0.5       # live: the cap is mandatory
    python -m eval.support_calibration --n 40 --dry-run            # scripted provider, no network
Exit 0 when agreement >= 0.80, 1 below, 2 on a usage error, 3 when the spend cap stopped the run.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any

from app.chat.citations import EvidenceReference, ResolvedSegment
from app.chat.support_check import PROMPT, check_support
from app.llm.errors import LlmError
from app.llm.gateway import LlmGateway, MemoryLedger, render_inputs
from app.llm.routing import Route

from eval.judge import (
    JudgeCase,
    JudgeOutputError,
    SpendCapReached,
    SpendGuard,
    check_tiers,
    run_judge,
    sample_cases,
)

AGREEMENT_TARGET = 0.80
OWNER = uuid.UUID(int=0)


@dataclass(frozen=True)
class Pair:
    case_id: str
    check_supported: bool
    judge_pass: bool


@dataclass(frozen=True)
class Report:
    n: int
    errors: int
    agreement: float
    kappa: float
    spent: Decimal
    disagreeing: tuple[str, ...]


def cohens_kappa(pairs: Sequence[tuple[bool, bool]]) -> float:
    """Cohen's kappa for two binary raters; NaN when there are no pairs, 1.0 when chance agreement is 1."""
    n = len(pairs)
    if n == 0:
        return math.nan
    po = sum(a == b for a, b in pairs) / n
    pa, pb = sum(a for a, _ in pairs) / n, sum(b for _, b in pairs) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    return 1.0 if pe == 1 else (po - pe) / (1 - pe)


def _segment(case: JudgeCase) -> ResolvedSegment:
    seg = case.segments[0]
    refs = tuple(
        EvidenceReference(uuid.uuid4(), uuid.uuid4(), i, i + 1, 0, len(p), p)
        for i, p in enumerate(seg.passages)
    )
    return ResolvedSegment(0, seg.text, "cited" if refs else "none", refs)


def _worst_case(gw: LlmGateway, segment: ResolvedSegment) -> Decimal:
    from app.chat.support_check import build_inputs

    p = gw.prompts.get(*PROMPT)
    res = gw.router.resolve("support_check", None, default=Route(p.role, p.effort))
    price = gw.prices.price(res.model)
    in_tok = (len(p.body) + len(render_inputs(build_inputs([segment])))) // 4 + 1
    return (Decimal(in_tok) * price.input + Decimal(p.max_tokens) * price.output) / Decimal(1_000_000)


async def calibrate(gw: LlmGateway, cases: Sequence[JudgeCase], guard: SpendGuard) -> Report:
    """A failed call on either side counts as a disagreement (conservative)."""
    pairs: list[Pair] = []
    errors = 0
    for c in cases:
        c = replace(c, reference_quotes=())  # the judge sees the same cited text as the checker, nothing more
        seg = _segment(c)
        if seg.support != "cited":
            continue
        guard.check(_worst_case(gw, seg))
        try:
            res = await check_support(gw, [seg], owner_id=OWNER)
            verdict = res.verdicts[0].verdict
            if verdict == "unchecked":
                raise JudgeOutputError("support check failed")
            j = await run_judge(gw, c, "faithfulness", guard=guard)
        except (LlmError, JudgeOutputError):
            errors += 1
            pairs.append(Pair(c.item_id, False, True))  # forced disagreement
            continue
        pairs.append(Pair(c.item_id, verdict == "supported", j.verdict == "pass"))
    raw = [(p.check_supported, p.judge_pass) for p in pairs]
    agree = sum(a == b for a, b in raw) / len(raw) if raw else math.nan
    bad = tuple(p.case_id for p in pairs if p.check_supported != p.judge_pass)
    return Report(len(pairs), errors, agree, cohens_kappa(raw), guard.spent, bad)


def _dry_gateway(ledger: MemoryLedger) -> LlmGateway:
    from app.core.settings import Settings
    from app.llm.pricing import DEFAULT_PRICES, PriceTable
    from app.llm.prompts import default_registry
    from app.llm.providers.fake import FakeProvider
    from app.llm.types import ChatRequest, ChatResponse, TextPart, Usage

    class DryProvider(FakeProvider):
        """Valid all-positive answers for the two schemas in play; no network."""

        async def complete(self, req: ChatRequest) -> ChatResponse:
            self.requests.append(req)
            props = (req.output_schema or {}).get("properties", {})
            if "verdicts" in props:
                data = json.loads(
                    str(req.messages[0]["content"]).removeprefix("<inputs>").removesuffix("</inputs>")
                )
                body: dict[str, Any] = {
                    "verdicts": [
                        {"index": s["index"], "verdict": "supported", "reason": "dry run"}
                        for s in data["segments"]
                    ]
                }
            else:
                body = {"reason": "dry run", "verdict": "pass", "unsupported_claims": [], "confidence": 0.9}
            return ChatResponse((TextPart(json.dumps(body)),), "end_turn", Usage(800, 80), None, req.model)

    settings = Settings(_env_file=None, database_url="postgresql+asyncpg://x/y", redis_url="redis://x")  # type: ignore[call-arg]
    return LlmGateway(
        chat=DryProvider(),
        prompts=default_registry(),
        prices=PriceTable({**DEFAULT_PRICES}),
        ledger=ledger,
        settings=settings,
    )


async def _run(n: int, split: str, gw: LlmGateway, guard: SpendGuard) -> int:
    from eval.loader import load_dataset

    cases = sample_cases(load_dataset().select(split), "citation", n)[:n]
    r = await calibrate(gw, cases, guard)
    rate = "n/a" if math.isnan(r.agreement) else f"{r.agreement:.2f}"
    kappa = "n/a" if math.isnan(r.kappa) else f"{r.kappa:.2f}"
    ok = r.n > 0 and r.agreement >= AGREEMENT_TARGET
    print(f"support_check@1 vs eval_judge_faithfulness@1: n={r.n} errors={r.errors} agreement={rate} "
          f"kappa={kappa} {'OK' if ok else 'BELOW 0.80'}")  # fmt: skip
    if r.disagreeing:
        print(f"  disagreeing: {', '.join(r.disagreeing)}")
    print(f"spent ${r.spent} of cap ${guard.cap}")
    return 0 if ok else 1


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="eval.support_calibration")
    ap.add_argument("--n", type=int, default=40, help="segments to compare")
    ap.add_argument("--split", default="train", choices=["train", "test", "smoke", "all"])
    ap.add_argument("--max-usd", type=Decimal, default=None, help="spend cap (mandatory for live calls)")
    ap.add_argument("--dry-run", action="store_true", help="scripted provider, no network")
    a = ap.parse_args(argv)
    if a.n < 1:
        ap.error("--n must be >= 1")
    if not a.dry_run and a.max_usd is None:
        print("refusing live calls without an explicit --max-usd cap", file=sys.stderr)
        return 2
    ledger = MemoryLedger()
    if a.dry_run:
        gw = _dry_gateway(ledger)
    else:
        from eval.judge import _live_gateway

        gw = _live_gateway(ledger)
    guard = SpendGuard(ledger, a.max_usd if a.max_usd is not None else Decimal(1000))
    try:
        check_tiers(gw.router)
        return asyncio.run(_run(a.n, a.split, gw, guard))
    except SpendCapReached as e:
        print(f"stopped: {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
