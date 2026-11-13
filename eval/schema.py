"""Vault-Eval schema (design section 18, plan S2.12/S2.13): golden items and the pipeline I/O types.

Golden items are validated strictly: unknown keys are rejected (a misspelled `must_not_contian` must not
silently disable a grader), required fields are enforced per category and per grading mode.

The second half of the module holds the plain dataclasses that cross the pipeline boundary (see
`eval.run` for the protocol): `Query` (what the pipeline may see: no gold), `ItemOutput` (what it returns)
and `EvalContext`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

Category = Literal[
    "lookup",
    "unanswerable",
    "compare",
    "multi_turn",
    "conflict",
    "identifier",
    "table",
    "summarize",
    "meta",
    "injection",
]
Grading = Literal[
    "judge", "abstention", "rubric", "exact_meta", "set_meta", "judge_per_turn", "judge+must_not_contain"
]
Coverage = Literal["supported", "partial", "insufficient"]
Match = Literal["exact", "fuzzy", "not_found"]
Split = Literal["train", "test", "smoke"]

# Gradings whose items need no gold evidence quote.
NO_EVIDENCE_GRADING = {"abstention", "rubric", "exact_meta", "set_meta"}


class Gold(BaseModel):
    """One verbatim evidence quote (chunker-independent) with the page it was located on."""

    model_config = ConfigDict(extra="forbid")
    quote: str = Field(min_length=1)
    page: int | None = Field(ge=1)  # None for flow documents (DOCX / Markdown); the key itself is required
    match: Match
    score: float | None = None
    doc_id: str | None = None
    labelled_page: int | None = None


class Turn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1)
    expected_answer: str = Field(min_length=1)
    gold: list[Gold]


class GoldenItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    source: str
    category: Category
    doc_ids: list[str] | None  # None = whole vault; the key itself is required
    expected_coverage: Coverage
    grading: Grading
    status: str
    question: str | None = None
    expected_answer: str | list[str] | None = None
    answer_type: str | None = None
    gold: list[Gold] = Field(default_factory=list)
    turns: list[Turn] | None = None
    rubric: list[str] | None = None
    expected_conflict: bool | None = None
    must_not_contain: list[str] | None = None
    dimensions: list[str] | None = None
    notes: str | None = None
    cuad_category: str | None = None
    fb_question_type: str | None = None
    fb_reasoning: str | None = None
    reference_abstract: str | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        err = self._errors()
        if err:
            raise ValueError(f"{self.id}: " + "; ".join(err))
        return self

    def _errors(self) -> list[str]:
        e: list[str] = []
        if self.category == "multi_turn":
            if self.grading != "judge_per_turn":
                e.append("multi_turn requires grading judge_per_turn")
            if not self.turns or len(self.turns) < 2:
                e.append("multi_turn requires at least 2 turns")
            if self.question is not None or self.gold:
                e.append("multi_turn carries question/gold inside turns, not at item level")
        else:
            if self.turns:
                e.append("turns only allowed for multi_turn items")
            if not self.question:
                e.append("question is required")
            if self.expected_answer in (None, "", []):
                e.append("expected_answer is required")
            if self.grading == "judge_per_turn":
                e.append("judge_per_turn only for multi_turn")
            if (
                not self.gold
                and self.grading not in NO_EVIDENCE_GRADING
                and self.expected_coverage != "insufficient"
            ):
                e.append("answerable judged items need at least one gold quote")
        if (self.category == "unanswerable") != (self.expected_coverage == "insufficient"):
            e.append("category unanswerable <=> expected_coverage insufficient")
        if (self.category == "unanswerable") != (self.grading == "abstention"):
            e.append("category unanswerable <=> grading abstention")
        if self.category == "unanswerable" and self.gold:
            e.append("unanswerable items carry no gold")
        if "must_not_contain" in self.grading and not self.must_not_contain:
            e.append("grading names must_not_contain but the list is empty")
        if self.must_not_contain is not None and "must_not_contain" not in self.grading:
            e.append("must_not_contain list set but grading does not apply it")
        if self.must_not_contain is not None and any(not s.strip() for s in self.must_not_contain):
            e.append("must_not_contain entries must be non-blank")
        if self.grading == "rubric" and not self.rubric:
            e.append("rubric grading requires a rubric")
        if self.category == "conflict" and self.expected_conflict is None:
            e.append("conflict items require expected_conflict (bool)")
        if self.category != "conflict" and self.expected_conflict is not None:
            e.append("expected_conflict only for conflict items")
        if self.grading == "set_meta" and not isinstance(self.expected_answer, list):
            e.append("set_meta expects a list expected_answer")
        if self.grading == "exact_meta" and not isinstance(self.expected_answer, str):
            e.append("exact_meta expects a string expected_answer")
        golds = list(self.gold) + [g for t in self.turns or [] for g in t.gold]
        for g in golds:
            if g.doc_id is None and not (self.doc_ids and len(self.doc_ids) == 1):
                e.append(f"gold quote {g.quote[:30]!r} has no doc_id and the item scope is not one document")
        return e

    def gold_doc(self, g: Gold) -> str:
        """The document a gold quote belongs to: its own `doc_id`, else the item's single scope document."""
        if g.doc_id is not None:
            return g.doc_id
        assert self.doc_ids and len(self.doc_ids) == 1  # enforced by the validator
        return self.doc_ids[0]

    def referenced_docs(self) -> set[str]:
        """Scope documents plus every gold document (multi-turn items name theirs only in the gold)."""
        out = set(self.doc_ids or [])
        for g in self.gold:
            out.add(self.gold_doc(g))
        for t in self.turns or []:
            for g in t.gold:
                out.add(self.gold_doc(g))
        return out

    def turn_item(self, n: int) -> GoldenItem:
        """Turn `n` (0-based) as a standalone single-turn item `<id>#t<n+1>` for grading."""
        assert self.turns is not None
        t = self.turns[n]
        gold = [g if g.doc_id else g.model_copy(update={"doc_id": self.gold_doc(g)}) for g in t.gold]
        # A derived view for grading, not a dataset item: unvalidated (a turn may lack gold).
        return GoldenItem.model_construct(
            id=f"{self.id}#t{n + 1}",
            source=self.source,
            category="lookup",
            doc_ids=self.doc_ids,
            expected_coverage=self.expected_coverage,
            grading="judge",
            status=self.status,
            question=t.question,
            expected_answer=t.expected_answer,
            gold=gold,
        )


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
    meta: str | list[str] | None = None  # structured result of a vault-metadata route
    conflict_flagged: bool | None = None
    latency_ms: float | None = None
    cost_usd: float | None = None

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
