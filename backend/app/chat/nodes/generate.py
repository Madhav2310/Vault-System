"""Generate node, non-streaming consumption (design §8.3, §8.5).

Streaming to the client arrives in S5; this node already uses `gateway.stream` and consumes it to completion,
so S5 only changes the consumer.

Request layout (cache-friendly): the system prompt and strict schema are the static prefix; the user message
puts the evidence first and the question last. The model's output is validated against the strict schema and
its citations are resolved by code (`citations.resolve`): the model never supplies quoted text.
"""

import json
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from pydantic import ValidationError

from app.chat.citations import Resolution, resolve
from app.chat.evidence import EvidenceSet, Mode, answer_schema
from app.chat.memory import Memory
from app.chat.nodes_common import defuse
from app.chat.segments_reader import salvage
from app.llm.errors import RefusalError, TruncatedError
from app.llm.gateway import Budget, LlmGateway
from app.llm.types import Final, TextDelta, Usage

STOP_REFUSAL = "STOP_REFUSAL"
STOP_MAX_TOKENS = "STOP_MAX_TOKENS"
GENERATION_INVALID = "GENERATION_INVALID"
PROMPT = ("answer_rag", 1)


@dataclass(frozen=True)
class GenerateResult:
    response_type: str  # answer | abstain | error
    resolution: Resolution
    reasons: tuple[str, ...]
    usage: Usage
    cost_usd: Decimal
    call_ids: tuple[uuid.UUID, ...]

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
) -> str:
    """Evidence first (stable for FULL_CONTEXT prefix caching), the question last."""
    parts = [evidence.text, history_block(memory), f"<route>{defuse(route)}</route>"]
    if coverage:
        parts.append(f"<coverage>\n{defuse(coverage)}\n</coverage>")
    if conflicts:
        parts.append(f"<conflicts>\n{defuse(conflicts)}\n</conflicts>")
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
    memory: Memory | None = None,
    coverage: str | None = None,
    conflicts: str | None = None,
    run_id: uuid.UUID | None = None,
    budget: Budget | None = None,
) -> GenerateResult:
    schema = answer_schema(evidence, mode)
    message = compose_user_message(evidence, question, route, memory, coverage, conflicts)
    chunks: list[str] = []
    final: Final | None = None
    reasons: list[str] = []
    try:
        async for ev in gateway.stream(
            purpose="generate",
            lane="interactive",
            prompt=PROMPT,
            owner_id=owner_id,
            schema=schema,
            route=route,
            mode=mode,
            messages=[{"role": "user", "content": message}],
            run_id=run_id,
            budget=budget,
        ):
            if isinstance(ev, TextDelta):
                chunks.append(ev.text)
            elif isinstance(ev, Final):
                final = ev
    except RefusalError:
        return _failed(STOP_REFUSAL)
    except TruncatedError:
        # Keep what finished: complete segments are intact; the cut-off one is dropped.
        status, segments = salvage("".join(chunks))
        if status == "abstain" or not segments:
            return _failed(STOP_MAX_TOKENS)
        return GenerateResult(
            "answer", resolve(segments, evidence), (STOP_MAX_TOKENS,), Usage(), Decimal(0), ()
        )
    assert final is not None
    raw = "".join(chunks)
    try:
        out: Any = schema.model_validate_json(raw)
    except ValidationError:
        return _failed(GENERATION_INVALID, final)
    if out.status == "abstain" or not out.segments:
        return GenerateResult(
            "abstain", Resolution(()), tuple(reasons), final.usage, final.cost_usd, final.call_ids
        )
    resolution = resolve(out.segments, evidence)
    reasons += list(resolution.reasons)
    return GenerateResult("answer", resolution, tuple(reasons), final.usage, final.cost_usd, final.call_ids)


def _failed(reason: str, final: Final | None = None) -> GenerateResult:
    return GenerateResult(
        "error",
        Resolution(()),
        (reason,),
        final.usage if final else Usage(),
        final.cost_usd if final else Decimal(0),
        final.call_ids if final else (),
    )
