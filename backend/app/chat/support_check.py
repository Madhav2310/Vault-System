"""Support check (design §8.5, S17.09): does each cited segment follow from its own citations?

One `fast` strict-schema call over all cited segments of the answer. The model sees, per segment, the
segment text and the `cited_text` of that segment's references (copied from stored sentences by the citation
resolver), and nothing else: never uncited text, never another segment's citations. The result is advisory:
any provider/model failure, timeout or budget stop yields `unchecked` for every segment plus the reason code
`SUPPORT_CHECK_FAILED`; the answer is never blocked. Verdicts for unknown indexes are dropped, a segment
without a verdict is `unchecked`, and the reason text is capped by code.
"""

import asyncio
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.chat.citations import ResolvedSegment
from app.chat.nodes_common import defuse
from app.core.logging import get_logger
from app.llm.errors import LlmError
from app.llm.gateway import Budget, LlmGateway

_log = get_logger("vault.chat.support_check")

PROMPT: tuple[str, int] = ("support_check", 1)

SUPPORT_CHECK_FAILED = "SUPPORT_CHECK_FAILED"
UNSUPPORTED_CLAIM = "UNSUPPORTED_CLAIM"

SUPPORTED, PARTIAL, UNSUPPORTED, UNCHECKED = "supported", "partial", "unsupported", "unchecked"
MAX_REASON_CHARS = 120
MAX_SEGMENTS = 12  # segments beyond this stay unchecked (bounds the prompt and the cost)
MAX_CLAIM_CHARS = 1200
MAX_CITED_CHARS = 1500  # per cited sentence range
MAX_CITED_PER_SEGMENT = 6
TIMEOUT_S = 10.0  # the check never uses more than this, nor more than the turn has left
MIN_REMAINING_S = 1.0  # below this the check is skipped (all unchecked) rather than started


class VerdictOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    index: int
    verdict: Literal["supported", "partial", "unsupported"]
    reason: str


class SupportOut(BaseModel):
    """Strict-mode compatible: every field required, no defaults."""

    model_config = ConfigDict(extra="forbid")
    verdicts: list[VerdictOut]


@dataclass(frozen=True)
class Verdict:
    verdict: str
    reason: str | None = None


@dataclass(frozen=True)
class SupportResult:
    verdicts: dict[int, Verdict]  # keyed by segment ordinal; one entry per segment passed in
    reasons: tuple[str, ...] = ()
    cost_usd: Decimal = Decimal(0)
    call_ids: tuple[uuid.UUID, ...] = ()
    degraded: tuple[str, ...] = field(default=())
    called: bool = False


def _cap(s: str, n: int) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def build_inputs(segments: Sequence[ResolvedSegment]) -> dict[str, list[dict[str, object]]]:
    """Only `support == cited` segments, each with its own cited sentences. Uncited text never goes in."""
    items: list[dict[str, object]] = []
    for seg in segments:
        if seg.support != "cited" or not seg.references:
            continue
        items.append(
            {
                "index": seg.ordinal,
                "claim": defuse(_cap(seg.text, MAX_CLAIM_CHARS)),
                "cited": [
                    defuse(_cap(r.cited_text, MAX_CITED_CHARS))
                    for r in seg.references[:MAX_CITED_PER_SEGMENT]
                ],
            }
        )
        if len(items) >= MAX_SEGMENTS:
            break
    return {"segments": items}


def _unchecked(segments: Sequence[ResolvedSegment]) -> dict[int, Verdict]:
    return {s.ordinal: Verdict(UNCHECKED) for s in segments}


def apply_verdicts(
    segments: Sequence[ResolvedSegment], out: SupportOut, asked: set[int]
) -> dict[int, Verdict]:
    """Pure repair: unknown or unasked indexes are dropped, the first verdict per index wins, a segment with
    no verdict (and every uncited segment) is `unchecked`, reasons are capped."""
    result = _unchecked(segments)
    seen: set[int] = set()
    for v in out.verdicts:
        if v.index not in asked or v.index in seen:
            continue
        seen.add(v.index)
        result[v.index] = Verdict(v.verdict, _cap(v.reason, MAX_REASON_CHARS) or None)
    return result


async def check_support(
    gateway: LlmGateway,
    segments: Sequence[ResolvedSegment],
    *,
    owner_id: uuid.UUID,
    run_id: uuid.UUID | None = None,
    budget: Budget | None = None,
    timeout_s: float = TIMEOUT_S,
) -> SupportResult:
    """Never raises for model/provider/budget failures. No call is made when no segment is cited."""
    inputs = build_inputs(segments)
    asked = {int(i["index"]) for i in inputs["segments"]}  # type: ignore[call-overload]
    if not asked:
        return SupportResult(_unchecked(segments))
    deadline = budget.deadline_at if budget is not None else None
    remaining = timeout_s if deadline is None else deadline - time.monotonic()
    if remaining < MIN_REMAINING_S:
        return SupportResult(_unchecked(segments), (SUPPORT_CHECK_FAILED,))
    try:
        async with asyncio.timeout(min(timeout_s, remaining - 0.5)):
            res = await gateway.call(
                purpose="support_check",
                lane="interactive",
                prompt=PROMPT,
                inputs=inputs,
                schema=SupportOut,
                owner_id=owner_id,
                run_id=run_id,
                budget=budget,
            )
    except (LlmError, TimeoutError) as e:
        _log.warning("support_check_failed", error_type=type(e).__name__)
        return SupportResult(_unchecked(segments), (SUPPORT_CHECK_FAILED,), called=True)
    assert res.output is not None  # schema calls either parse or raise
    return SupportResult(
        apply_verdicts(segments, res.output, asked),
        cost_usd=res.cost_usd,
        call_ids=res.call_ids,
        degraded=res.degraded,
        called=True,
    )
