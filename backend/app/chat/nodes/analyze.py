"""`analyze_query` node (design 8.3, 19): one `fast` strict-schema call, validated and narrowed in code.

The model sees the question, a trimmed history, session memory and document *titles* (as short refs, never
ids or text). It returns a typed decision; this module applies it: unknown or foreign refs are dropped (the
node can narrow the scope, never widen it), the intent is restricted to the route enum, free text is capped,
and a `clarify` intent becomes `qa` (`CLARIFY_REJECTED`): the prompt's input says clarifications are not
allowed, and none is ever asked. Any gateway failure (schema violation, refusal, truncation, timeout, budget)
yields a deterministic fallback decision.
"""

import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from app.chat.memory import Memory, Msg
from app.chat.meta import META_OP_REJECTED, MetaOp, MetaOpOut, validate_op
from app.core.logging import get_logger
from app.llm.errors import LlmError
from app.llm.gateway import Budget, LlmGateway

_log = get_logger("vault.chat.analyze")

PROMPT: tuple[str, int] = ("analyze_query", 4)

# Static first: what rarely changes within a session comes first and the question last, so the
# provider's prompt cache covers the system prompt plus the documents/summary/history prefix.
INPUT_ORDER = (
    "documents",
    "resolved_entities",
    "rolling_summary",
    "history",
    "clarification_allowed",
    "question",
)


def prompt_for(gateway: LlmGateway) -> tuple[str, int]:
    return PROMPT


def render_ordered(inputs: Mapping[str, Any]) -> str:
    """The user turn: the `<inputs>` JSON the gateway would render, with keys in `INPUT_ORDER`."""
    assert set(inputs) == set(INPUT_ORDER)
    ordered = {k: inputs[k] for k in INPUT_ORDER}
    return f"<inputs>{json.dumps(ordered, ensure_ascii=False, default=str)}</inputs>"


CLARIFY_REJECTED = "CLARIFY_REJECTED"
TARGET_OUT_OF_SCOPE = "TARGET_OUT_OF_SCOPE"
ANALYZE_FALLBACK = "ANALYZE_FALLBACK"

MAX_QUESTION_CHARS = 2000  # sent to the model
MAX_STANDALONE_CHARS = 500
MAX_DIMENSIONS = 5
MAX_DIMENSION_CHARS = 80
HISTORY_TURNS = 4
HISTORY_MSG_CHARS = 500
MAX_DOCUMENTS = 100
MAX_TITLE_CHARS = 120
MAX_ENTITIES = 20
MAX_SUMMARY_CHARS = 1500

Intent = Literal["qa", "compare", "summarize", "meta", "chitchat", "clarify"]


class ClarificationOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["document", "query"]
    question: str
    options: list[str]


class AnalyzeQueryOut(BaseModel):
    """Strict-mode compatible: every field required, no defaults, no unions beyond `T | None`.
    Field order is the generation order: the rewrite comes before the labels it informs."""

    model_config = ConfigDict(extra="forbid")
    standalone_query: str
    target_refs: list[str]
    dimensions: list[str]
    intent: Intent
    needs_whole_document: bool
    clarification: ClarificationOut | None
    meta_op: MetaOpOut | None = None  # strict mode still requires it; the default keeps scripted tests small


@dataclass(frozen=True)
class ScopeDoc:
    id: uuid.UUID
    title: str


@dataclass(frozen=True)
class AnalyzeResult:
    intent: Intent
    standalone_query: str
    target_document_ids: tuple[uuid.UUID, ...]  # always a subset of the documents passed in
    dimensions: tuple[str, ...]
    needs_whole_document: bool
    reason_codes: tuple[str, ...]
    fallback: bool = False
    dropped_targets: int = 0
    cost_usd: Decimal = Decimal(0)
    call_ids: tuple[uuid.UUID, ...] = ()
    degraded: tuple[str, ...] = ()
    meta_op: MetaOp | None = None  # set only with intent == "meta" (validated against the allowlist)


def _cap(s: str, n: int) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _history(memory: Memory) -> list[dict[str, str]]:
    msgs: list[Msg] = [m for turn in memory.turns[-HISTORY_TURNS:] for m in turn]
    return [{"role": m.role, "content": _cap(m.content, HISTORY_MSG_CHARS)} for m in msgs]


def _entities(raw: Mapping[str, Any], id_to_ref: Mapping[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in list(raw.items())[:MAX_ENTITIES]:
        val = str(v)
        out[_cap(str(k), 80)] = id_to_ref.get(val, _cap(val, 200))  # a document id becomes its ref
    return out


def build_inputs(question: str, memory: Memory, documents: Sequence[ScopeDoc]) -> dict[str, Any]:
    docs = list(documents)[:MAX_DOCUMENTS]
    id_to_ref = {str(d.id): f"D{i}" for i, d in enumerate(docs, 1)}
    summary = _cap(memory.rolling_summary, MAX_SUMMARY_CHARS) if memory.rolling_summary else None
    return {
        "question": question[:MAX_QUESTION_CHARS],
        "history": _history(memory),
        "rolling_summary": summary,
        "resolved_entities": _entities(memory.resolved_entities, id_to_ref),
        "documents": [
            {"ref": f"D{i}", "title": _cap(d.title, MAX_TITLE_CHARS)} for i, d in enumerate(docs, 1)
        ],
        "clarification_allowed": False,  # part of the analyze_query@4 input: Vault never asks back
    }


def _fallback(question: str, error: LlmError) -> AnalyzeResult:
    _log.warning("analyze_query_fallback", error_type=error.error_type)
    return AnalyzeResult(
        intent="qa",
        standalone_query=_cap(question, MAX_STANDALONE_CHARS),
        target_document_ids=(),
        dimensions=(),
        needs_whole_document=False,
        reason_codes=(ANALYZE_FALLBACK,),
        fallback=True,
    )


def _doc_keys(documents: Sequence[ScopeDoc]) -> dict[str, uuid.UUID]:
    by_key: dict[str, uuid.UUID] = {}
    for i, d in enumerate(list(documents)[:MAX_DOCUMENTS], 1):
        by_key[f"D{i}"] = d.id
        by_key[str(d.id)] = d.id
    return by_key


def _resolve_targets(refs: Sequence[str], documents: Sequence[ScopeDoc]) -> tuple[tuple[uuid.UUID, ...], int]:
    by_key = _doc_keys(documents)
    kept: list[uuid.UUID] = []
    dropped = 0
    for r in refs:
        doc_id = by_key.get(r.strip())
        if doc_id is None:
            dropped += 1
        elif doc_id not in kept:
            kept.append(doc_id)
    return tuple(kept), dropped


def apply_decision(out: AnalyzeQueryOut, question: str, documents: Sequence[ScopeDoc]) -> AnalyzeResult:
    """Pure validation of a parsed model decision against the allowed documents."""
    codes: list[str] = []
    targets, dropped = _resolve_targets(out.target_refs, documents)
    if dropped:
        codes.append(TARGET_OUT_OF_SCOPE)
    standalone = _cap(out.standalone_query, MAX_STANDALONE_CHARS) or _cap(question, MAX_STANDALONE_CHARS)
    dims = tuple(dict.fromkeys(d for d in (_cap(x, MAX_DIMENSION_CHARS) for x in out.dimensions) if d))
    dims = dims[:MAX_DIMENSIONS]
    intent: Intent = out.intent
    if intent == "clarify":
        intent = "qa"
        codes.append(CLARIFY_REJECTED)
    meta_op: MetaOp | None = None
    if intent == "meta":
        meta_op = validate_op(out.meta_op, _doc_keys(documents))
        if meta_op is None:  # unknown op or unusable parameters: answer it as an ordinary question
            intent = "qa"
            codes.append(META_OP_REJECTED)
    if intent in ("meta", "chitchat"):
        targets, dims = (), ()
    return AnalyzeResult(
        intent=intent,
        standalone_query=standalone,
        target_document_ids=targets,
        dimensions=dims,
        needs_whole_document=out.needs_whole_document and intent in ("qa", "compare", "summarize"),
        reason_codes=tuple(codes),
        dropped_targets=dropped,
        meta_op=meta_op,
    )


async def analyze_query(
    gateway: LlmGateway,
    *,
    question: str,
    memory: Memory,
    documents: Sequence[ScopeDoc],
    owner_id: uuid.UUID,
    run_id: uuid.UUID | None = None,
    budget: Budget | None = None,
) -> AnalyzeResult:
    """Never raises for model or provider failures (fallback decision); programming errors still propagate."""
    inputs = build_inputs(question, memory, documents)
    try:
        res = await gateway.call(
            purpose="analyze_query",
            lane="interactive",
            prompt=prompt_for(gateway),
            messages=[{"role": "user", "content": render_ordered(inputs)}],
            schema=AnalyzeQueryOut,
            owner_id=owner_id,
            run_id=run_id,
            budget=budget,
        )
    except LlmError as e:
        return _fallback(question, e)
    assert res.output is not None  # schema calls either parse or raise
    result = replace(
        apply_decision(res.output, question, documents),
        cost_usd=res.cost_usd,
        call_ids=res.call_ids,
        degraded=res.degraded,
    )
    _log.info(
        "analyze_query",
        intent=result.intent,
        targets=len(result.target_document_ids),
        dropped_targets=result.dropped_targets,
        reason_codes=list(result.reason_codes),
    )
    return result
