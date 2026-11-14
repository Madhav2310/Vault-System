"""Generate node, non-streaming consumption.

Streaming to the client arrives in S5; this node already uses `gateway.stream` and consumes it to completion,
so S5 only changes the consumer.

Request layout (cache-friendly): the system prompt and strict schema are the static prefix; the user message
puts the evidence first and the question last. The model's output is validated against the strict schema and
its citations are resolved by code (`citations.resolve`): the model never supplies quoted text.
"""

import json
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from pydantic import ValidationError

from app.chat.calc import resolve_calc, resolve_calc_segment
from app.chat.citations import Resolution, ResolvedSegment, resolve, resolve_segment
from app.chat.conflicts import CONFLICT_DETECTED, ResolvedConflict, resolve_conflicts
from app.chat.coverage import apply_plan, check_plan, element_ids, elements_block
from app.chat.evidence import EvidenceSet, Mode, answer_schema, cache_mode_for
from app.chat.memory import Memory
from app.chat.nodes_common import defuse
from app.chat.segments_reader import SegmentReader, salvage
from app.llm.errors import LlmError, RefusalError, TruncatedError
from app.llm.gateway import Budget, LlmGateway
from app.llm.types import Final, TextDelta, Usage

STOP_REFUSAL = "STOP_REFUSAL"
STOP_MAX_TOKENS = "STOP_MAX_TOKENS"
GENERATION_INVALID = "GENERATION_INVALID"
PROMPT: tuple[str, int] = ("answer_rag", 12)  # completeness-first with detail discipline


def answer_prompt(settings: Any = None) -> tuple[str, int]:
    """The answer prompt of a qa turn (compare, plan-first and calc turns select their own)."""
    return PROMPT


@dataclass(frozen=True)
class GenerateResult:
    response_type: str  # answer | abstain | error
    resolution: Resolution
    reasons: tuple[str, ...]
    usage: Usage
    cost_usd: Decimal
    call_ids: tuple[uuid.UUID, ...]
    followups: tuple[str, ...] = ()  # as the model wrote them; the node cleans them (followups.py)
    conflicts: tuple[ResolvedConflict, ...] = ()  # validated by code (conflicts.py)
    not_found: int | None = None  # plan elements without a valid handle; None: no plan was checked

    @property
    def text(self) -> str:
        return " ".join(s.text for s in self.resolution.segments)


def history_block(memory: Memory | None) -> str:
    if memory is None or (not memory.turns and not memory.rolling_summary):
        return "<history>\n(none)\n</history>"
    lines: list[str] = []
    if memory.rolling_summary:
        lines.append(f"Summary of earlier turns: {memory.rolling_summary}")
    if memory.resolved_entities:
        lines.append("Resolved references: " + json.dumps(memory.resolved_entities, sort_keys=True))
    for turn in memory.turns:
        lines += [f"{m.role}: {m.content}" for m in turn]
    return "<history>\n" + defuse("\n".join(lines)) + "\n</history>"


def compose_user_message(
    evidence: EvidenceSet,
    question: str,
    route: str,
    memory: Memory | None = None,
    coverage: str | None = None,
    conflicts: str | None = None,
    elements: str | None = None,
) -> str:
    """Evidence first (stable for FULL_CONTEXT prefix caching), the question last."""
    parts = [evidence.text, history_block(memory), f"<route>{defuse(route)}</route>"]
    if coverage:
        parts.append(f"<coverage>\n{defuse(coverage)}\n</coverage>")
    if conflicts:
        parts.append(f"<conflicts>\n{defuse(conflicts)}\n</conflicts>")
    if elements:
        parts.append(f"<required_elements>\n{defuse(elements)}\n</required_elements>")
    parts.append(f"<question>\n{defuse(question)}\n</question>")
    return "\n\n".join(parts)


async def generate(
    gateway: LlmGateway,
    *,
    owner_id: uuid.UUID,
    evidence: EvidenceSet,
    question: str,
    route: str,
    mode: Mode = "rag",
    prompt: tuple[str, int] = PROMPT,
    memory: Memory | None = None,
    coverage: str | None = None,
    conflicts: str | None = None,
    plan_elements: Sequence[str] = (),
    calc: bool = False,
    system_suffix: str | None = None,
    run_id: uuid.UUID | None = None,
    budget: Budget | None = None,
    on_segment: Callable[[ResolvedSegment], Awaitable[None]] | None = None,
) -> GenerateResult:
    """`on_segment` receives each segment (citations resolved by code) as soon as its JSON object closes in
    the stream, before the whole answer has arrived. The final result is resolved from the complete, validated
    answer and is what gets persisted."""
    plan_ids = element_ids(len(plan_elements)) if mode == "rag" else ()
    calc = calc and mode == "rag"  # typed calculation segments
    schema = answer_schema(evidence, mode, plan_ids, calc)
    elements = elements_block(plan_elements) if plan_ids else None
    message = compose_user_message(evidence, question, route, memory, coverage, conflicts, elements)
    chunks: list[str] = []
    final: Final | None = None
    reasons: list[str] = []
    reader, streamed = SegmentReader(), 0
    try:
        async for ev in gateway.stream(
            purpose="generate",
            lane="interactive",
            prompt=prompt,
            owner_id=owner_id,
            schema=schema,
            route=route,
            mode=mode,
            cache_mode=cache_mode_for(mode),
            messages=[{"role": "user", "content": message}],
            run_id=run_id,
            budget=budget,
            system_suffix=system_suffix,
        ):
            if isinstance(ev, TextDelta):
                chunks.append(ev.text)
                if on_segment is not None:
                    for done_seg in reader.feed(ev.text):
                        if reader.status == "answer" and _well_formed(done_seg):
                            seg = (
                                resolve_calc_segment(streamed, done_seg, evidence)[0]
                                if calc
                                else resolve_segment(streamed, done_seg, evidence)
                            )
                            await on_segment(seg)
                            streamed += 1
            elif isinstance(ev, Final):
                final = ev
    except RefusalError as e:
        return _failed(STOP_REFUSAL, e)
    except TruncatedError as e:
        # Keep what finished: complete segments are intact; the cut-off one is dropped.
        status, raw_segments = salvage("".join(chunks))
        segments = [g for g in raw_segments if _well_formed(g)]
        if status == "abstain" or not segments:
            return _failed(STOP_MAX_TOKENS, e)
        resolution = resolve_calc(segments, evidence) if calc else resolve(segments, evidence)
        return GenerateResult(
            "answer",
            resolution,
            (STOP_MAX_TOKENS, *resolution.reasons),
            e.usage or Usage(),
            e.cost_usd,
            e.call_ids,
        )
    assert final is not None
    raw = "".join(chunks)
    plan: Any = None
    try:
        out: Any = schema.model_validate_json(raw)
        plan = [p.model_dump() for p in out.plan] if plan_ids else None
    except ValidationError:
        # a bad plan or calculation never fails the answer
        out = _without_plan(raw, evidence, mode) if plan_ids or calc else None
        if out is None:
            return _failed(GENERATION_INVALID, final)
    if out.status == "abstain" or not out.segments:
        return GenerateResult(
            "abstain", Resolution(()), tuple(reasons), final.usage, final.cost_usd, final.call_ids
        )
    resolution = resolve_calc(out.segments, evidence) if calc else resolve(out.segments, evidence)
    reasons += list(resolution.reasons)
    check = check_plan(plan, plan_elements, evidence) if plan_ids else None
    if check is not None:
        resolution, plan_reasons = apply_plan(resolution, check)
        reasons += plan_reasons
    flagged = resolve_conflicts(out.conflicts, evidence)
    if flagged:
        reasons.append(CONFLICT_DETECTED)
    return GenerateResult(
        "answer",
        resolution,
        tuple(reasons),
        final.usage,
        final.cost_usd,
        final.call_ids,
        tuple(out.followups),
        flagged,
        None if check is None else len(check.not_found),
    )


def _without_plan(raw: str, evidence: EvidenceSet, mode: Mode) -> Any:
    """The answer validated against the plain schema with its `plan` removed, or None when that fails too."""
    try:
        data = json.loads(raw)
        data.pop("plan", None)
        for seg in data.get("segments", []):
            seg.pop("calc", None)
        return answer_schema(evidence, mode).model_validate(data)
    except (ValueError, AttributeError, ValidationError):
        return None


def _well_formed(seg: dict[str, Any]) -> bool:
    ev = seg.get("evidence")
    return isinstance(seg.get("text"), str) and isinstance(ev, list) and all(isinstance(h, str) for h in ev)


def _failed(reason: str, acct: Final | LlmError | None = None) -> GenerateResult:
    """An error result that keeps the usage, cost and call ids of the call that produced it."""
    usage = (acct.usage if acct else None) or Usage()
    return GenerateResult(
        "error",
        Resolution(()),
        (reason,),
        usage,
        acct.cost_usd if acct else Decimal(0),
        acct.call_ids if acct else (),
    )
