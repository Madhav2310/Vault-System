"""Vault-Eval schema: golden items and the pipeline I/O types.

Golden items are validated strictly: unknown keys are rejected (a misspelled `must_not_contian` must not
silently disable a grader), and the fields each stratum and answer type needs are enforced.

The second half of the module holds the plain dataclasses that cross the pipeline boundary (see
`eval.run` for the protocol): `Query` (what the pipeline may see: no gold), `ItemOutput` (what it returns)
and `EvalContext`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

# --- golden items: atomic claims, evidence as sentence ranges ---

Stratum = Literal[
    "single_span",
    "conditional",
    "table_lookup",
    "numeric_calc",
    "multi_span",
    "multi_hop",
    "compare_docs",
    "conflict",
    "aggregation",
    "unanswerable",
    "false_premise",
    "versioned",
    "injection",
    "multi_turn",
    "formatting",
]
AnswerType = Literal["answer", "abstain", "partial"]
AuditStatus = Literal["draft", "verified", "dropped"]
MAX_REQUIRED_CLAIMS = 6


class GoldEvidence(BaseModel):
    """Gold evidence: document sentences [sentence_start, sentence_end), half-open like `CitedRange`."""

    model_config = ConfigDict(extra="forbid")
    doc: str = Field(min_length=1)
    sentence_start: int = Field(ge=0)
    sentence_end: int = Field(ge=1)

    @model_validator(mode="after")
    def _range(self) -> Self:
        if self.sentence_end <= self.sentence_start:
            raise ValueError("sentence_end must be greater than sentence_start (half-open range)")
        return self


class GoldItem(BaseModel):
    """A golden item. Unknown keys are rejected; the per-document cap is a dataset-level check (loader)."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    stratum: Stratum
    question: str = Field(min_length=1)
    source_doc: str = Field(min_length=1)  # clustering key for the per-document cap and clustered statistics
    answer_type: AnswerType
    audit_status: AuditStatus = "draft"
    required_claims: list[str] = Field(default_factory=list)  # each checkable alone
    optional_claims: list[str] = Field(default_factory=list)
    alternates: dict[str, list[str]] = Field(default_factory=dict)  # claim text -> accepted variants
    gold_evidence: list[GoldEvidence] = Field(default_factory=list)
    must_not_contain: list[str] = Field(default_factory=list)
    doc_ids: list[str] | None = None  # scope; None = whole vault
    prior_turns: list[str] = Field(default_factory=list)  # earlier user turns of a multi_turn item
    audit_note: str | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        e: list[str] = []
        n = len(self.required_claims)
        if self.answer_type == "abstain":
            if n:
                e.append("abstain items must have no required_claims")
            if self.gold_evidence:
                e.append("abstain items carry no gold_evidence")
        elif not 1 <= n <= MAX_REQUIRED_CLAIMS:
            e.append(f"{self.answer_type} items need 1 to {MAX_REQUIRED_CLAIMS} required_claims, found {n}")
        if self.stratum == "unanswerable" and self.answer_type != "abstain":
            e.append("unanswerable stratum requires answer_type abstain")
        if self.stratum == "injection" and not self.must_not_contain:
            e.append("injection items require must_not_contain")
        if self.stratum == "multi_turn" and not self.prior_turns:
            e.append("multi_turn items require prior_turns")
        if self.stratum != "multi_turn" and self.prior_turns:
            e.append("prior_turns only for multi_turn items")
        if self.audit_status == "dropped" and not self.audit_note:
            e.append("dropped items require an audit_note giving the reason")
        for name in ("required_claims", "optional_claims", "must_not_contain", "prior_turns"):
            if any(not s.strip() for s in getattr(self, name)):
                e.append(f"{name} entries must be non-blank")
        known = set(self.required_claims) | set(self.optional_claims)
        e += [
            f"alternates key {k!r} is not one of the item's claims" for k in self.alternates if k not in known
        ]
        if any(not v or any(not a.strip() for a in v) for v in self.alternates.values()):
            e.append("alternates need non-blank variant lists")
        if e:
            raise ValueError(f"{self.id}: " + "; ".join(e))
        return self


# --- pipeline I/O (protocol documented in eval.run) ---


@dataclass(frozen=True)
class CitedRange:
    """A citation: sentences [sentence_start, sentence_end) (half-open indexes) of one chunk."""

    chunk_id: str
    sentence_start: int
    sentence_end: int


@dataclass(frozen=True)
class AnswerSegment:
    text: str
    citations: tuple[CitedRange, ...] = ()


@dataclass(frozen=True)
class Query:
    """What a pipeline may see about an item: never gold, rubric or expected answers."""

    item_id: str
    question: str
    category: str
    scope_docs: tuple[str, ...] | None  # eval document keys; None = whole vault
    turn_index: int = 0
    n_turns: int = 1


@dataclass
class ItemOutput:
    retrieved: list[str] = field(default_factory=list)  # ranked chunk ids, best first
    segments: list[AnswerSegment] = field(default_factory=list)
    abstained: bool = False
    latency_ms: float | None = None
    ttft_ms: float | None = None  # streamed runs: request start to the first segment event
    cost_usd: float | None = None
    degraded: tuple[str, ...] = ()  # e.g. lexical_only: the run measured a degraded configuration
    evidence: list[str] | None = None  # chunk ids the generator was given (selected candidates and citations)

    @property
    def answer_text(self) -> str:
        return "\n".join(s.text for s in self.segments)


@dataclass
class EvalContext:
    """Per-run (and per-turn) state handed to the pipeline."""

    owner_id: uuid.UUID | None = None
    doc_ids: dict[str, str] = field(default_factory=dict)  # eval document key -> ingested document id
    session_id: uuid.UUID | None = None  # set for multi-turn items: the one session all turns share
    history: list[tuple[Query, ItemOutput]] = field(default_factory=list)  # earlier turns, in order
    services: dict[str, Any] = field(default_factory=dict)
