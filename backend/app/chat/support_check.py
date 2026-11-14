"""Support check: does each cited segment follow from its own citations?

One `fast` strict-schema call over all cited segments of the answer. The model sees, per segment, the
segment text and the `cited_text` of that segment's references (copied from stored sentences by the citation
resolver), and nothing else: never uncited text, never another segment's citations. The result is advisory:
any provider/model failure, timeout or budget stop yields `unchecked` for every segment plus the reason code
`SUPPORT_CHECK_FAILED`; the answer is never blocked. Verdicts for unknown indexes are dropped, a segment
without a verdict is `unchecked`, and the reason text is capped by code.
"""

import asyncio
import dataclasses
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from app.chat.citations import ResolvedSegment, resolve_segment
from app.chat.evidence import EvidenceSet
from app.chat.nodes_common import defuse
from app.core.logging import get_logger
from app.llm.errors import LlmError
from app.llm.gateway import Budget, LlmGateway

_log = get_logger("vault.chat.support_check")

PROMPT: tuple[str, int] = ("support_check", 1)
PROMPT_V2: tuple[str, int] = ("support_check", 2)  # verdict + minimal needed handles; off by default

SUPPORT_CHECK_FAILED = "SUPPORT_CHECK_FAILED"
UNSUPPORTED_CLAIM = "UNSUPPORTED_CLAIM"
UNSUPPORTED_REMOVED = "UNSUPPORTED_REMOVED"  # an unsupported segment was removed from the answer
ANSWER_REGENERATED = "ANSWER_REGENERATED"  # every segment failed, the answer was written once more
UNSUPPORTED_ABSTAINED = "UNSUPPORTED_ABSTAINED"  # nothing supported survived: the warm abstain text

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


class VerdictOutV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    index: int
    verdict: Literal["supported", "partial", "unsupported"]
    contradicts: bool  # the passages say the opposite of the claim (an unsupported segment that is kept)
    needed: list[str]  # the minimal subset of the cited handles the claim rests on
    reason: str


class SupportOutV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verdicts: list[VerdictOutV2]


@dataclass(frozen=True)
class Verdict:
    verdict: str
    reason: str | None = None
    needed: tuple[str, ...] | None = None  # v2: validated subset of the shown handles; None = keep all
    contradicted: bool = False


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


def segment_handles(seg: ResolvedSegment, evidence: EvidenceSet) -> list[tuple[str, str]]:
    """Every sentence handle a segment cites, with its sentence text (from the stored chunk), in order."""
    out: list[tuple[str, str]] = []
    for r in seg.references:
        chunk = evidence.chunks[r.chunk_id]
        for i in range(r.sentence_start, r.sentence_end):
            a, b = chunk.sentences[i]
            out.append((f"{evidence.chunk_handles[r.chunk_id]}.{i}", chunk.original_text[a:b]))
    return out


def build_inputs_v2(
    segments: Sequence[ResolvedSegment], evidence: EvidenceSet
) -> dict[str, list[dict[str, object]]]:
    """As `build_inputs`, but `cited` is one `{handle, text}` per cited sentence."""
    items: list[dict[str, object]] = []
    for seg in segments:
        if seg.support != "cited" or not seg.references:
            continue
        shown = segment_handles(seg, evidence)[:MAX_CITED_PER_SEGMENT]
        items.append(
            {
                "index": seg.ordinal,
                "claim": defuse(_cap(seg.text, MAX_CLAIM_CHARS)),
                "cited": [{"handle": h, "text": defuse(_cap(t, MAX_CITED_CHARS))} for h, t in shown],
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


def apply_verdicts_v2(
    segments: Sequence[ResolvedSegment], out: SupportOutV2, inputs: dict[str, list[dict[str, object]]]
) -> dict[int, Verdict]:
    """As `apply_verdicts`, plus the `needed` handles: only handles that were shown for that segment are
    kept (in the order shown), an invented handle is dropped, and an empty result means `None` (keep all
    citations: the model can never prune a segment down to nothing, nor add a handle)."""
    shown: dict[int, list[str]] = {}
    for item in inputs["segments"]:
        cited: Any = item["cited"]
        shown[int(item["index"])] = [str(c["handle"]) for c in cited]  # type: ignore[call-overload]
    result = _unchecked(segments)
    seen: set[int] = set()
    for v in out.verdicts:
        if v.index not in shown or v.index in seen:
            continue
        seen.add(v.index)
        asked_handles = shown[v.index]
        want = set(v.needed)
        needed = tuple(h for h in asked_handles if h in want) if v.verdict != "unsupported" else ()
        result[v.index] = Verdict(
            v.verdict,
            _cap(v.reason, MAX_REASON_CHARS) or None,
            needed if needed and len(needed) < len(asked_handles) else None,
            v.contradicts and v.verdict == "unsupported",
        )
    return result


def prune_segment(seg: ResolvedSegment, needed: tuple[str, ...], evidence: EvidenceSet) -> ResolvedSegment:
    """Keep only the `needed` handles (plus any handle that was not shown to the checker), re-resolved by
    code from the manifest so `cited_text` is still copied from stored sentences."""
    all_handles = [h for h, _ in segment_handles(seg, evidence)]
    unshown = all_handles[MAX_CITED_PER_SEGMENT:]
    kept = [h for h in all_handles if h in needed or h in unshown]
    fresh = resolve_segment(seg.ordinal, {"text": seg.text, "evidence": kept}, evidence)
    return dataclasses.replace(seg, references=fresh.references, support=fresh.support)


@dataclass(frozen=True)
class Repair:
    segments: tuple[ResolvedSegment, ...]
    removed: tuple[int, ...]  # ordinals of removed segments
    pruned: int  # segments whose citations were pruned


def all_failed(segments: Sequence[ResolvedSegment], verdicts: dict[int, Verdict]) -> bool:
    """True when the answer has segments and every one of them was judged unsupported."""
    return bool(segments) and all(
        verdicts.get(s.ordinal, Verdict(UNCHECKED)).verdict == UNSUPPORTED for s in segments
    )


def repair(
    segments: Sequence[ResolvedSegment], verdicts: dict[int, Verdict], evidence: EvidenceSet
) -> Repair:
    """Pure: remove unsupported segments (a contradicted one is kept and stays flagged), prune citations
    the checker found unneeded. Segments keep their ordinals (the stream already used them as indexes)."""
    kept: list[ResolvedSegment] = []
    removed: list[int] = []
    pruned = 0
    for seg in segments:
        v = verdicts.get(seg.ordinal, Verdict(UNCHECKED))
        if v.verdict == UNSUPPORTED and not v.contradicted:
            removed.append(seg.ordinal)
            continue
        if v.needed is not None and v.verdict in (SUPPORTED, PARTIAL):
            new = prune_segment(seg, v.needed, evidence)
            if new.references != seg.references:
                pruned += 1
            seg = new
        kept.append(seg)
    return Repair(tuple(kept), tuple(removed), pruned)


async def check_support(
    gateway: LlmGateway,
    segments: Sequence[ResolvedSegment],
    *,
    owner_id: uuid.UUID,
    run_id: uuid.UUID | None = None,
    budget: Budget | None = None,
    timeout_s: float = TIMEOUT_S,
    evidence: EvidenceSet | None = None,
) -> SupportResult:
    """Never raises for model/provider/budget failures. No call is made when no segment is cited.
    `support_check@2` is used only when `chat_support_version` is 2 and `evidence` is given."""
    v2 = evidence is not None and gateway.settings.chat_support_version == 2
    inputs = build_inputs_v2(segments, evidence) if evidence is not None and v2 else build_inputs(segments)
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
                prompt=PROMPT_V2 if v2 else PROMPT,
                inputs=inputs,
                schema=SupportOutV2 if v2 else SupportOut,
                owner_id=owner_id,
                run_id=run_id,
                budget=budget,
            )
    except (LlmError, TimeoutError) as e:
        _log.warning("support_check_failed", error_type=type(e).__name__)
        return SupportResult(_unchecked(segments), (SUPPORT_CHECK_FAILED,), called=True)
    assert res.output is not None  # schema calls either parse or raise
    if v2:
        assert isinstance(res.output, SupportOutV2)
        verdicts = apply_verdicts_v2(segments, res.output, inputs)
    else:
        assert isinstance(res.output, SupportOut)
        verdicts = apply_verdicts(segments, res.output, asked)
    return SupportResult(
        verdicts,
        cost_usd=res.cost_usd,
        call_ids=res.call_ids,
        degraded=res.degraded,
        called=True,
    )
