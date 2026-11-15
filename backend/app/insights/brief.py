"""DocumentBrief: model output schema, code-side normalization and the stored form.

The model returns `BriefOut` in a strict schema: every field required, no defaults, `T | None` only. Each fact
names evidence *handles* (`E3.1`); in RAG-sized evidence sets the handle field is an enum of the handles in
the request, exactly as in `answer_schema`. Code then resolves the handles through the chat citation
resolver (so `cited_text` always comes from stored sentences, never from the model), normalizes and caps
the free-text fields, and verifies each fact (`verify.py`). The stored `Brief` is what the API serves.
"""

import hashlib
import json
import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, create_model

from app.chat.citations import resolve
from app.chat.evidence import EvidenceSet

FactField = Literal[
    "party",
    "effective_date",
    "expiry_date",
    "deadline",
    "amount",
    "obligation",
    "termination",
    "governing_law",
    "key_point",
    "risk",
    "other",
]
Category = Literal["contract", "financial_report", "research_paper", "policy", "correspondence", "other"]
Tone = Literal["neutral", "formal", "adversarial", "positive", "negative", "mixed"]

MAX_SUMMARY_CHARS = 1200
MAX_FACTS = 40
MAX_TAGS = 8
MAX_TAG_WORDS = 3
MAX_TAG_CHARS = 40
MAX_QUESTIONS = 6
MAX_QUESTION_CHARS = 200
MAX_DOCUMENT_TYPE_CHARS = 80
MAX_FACT_VALUE_CHARS = 400
MAX_FACT_EVIDENCE = 4  # references kept per fact (after a merge of duplicates)
MAX_TONE_NOTE_CHARS = 160
MAX_TONE_EVIDENCE = 3

KIND = "brief"  # document_insights.kind
PROMPT_VERSION = 2  # the stored brief shape (with tone) and brief_merge@2; part of the insight key


def params_hash(params: Mapping[str, Any]) -> str:
    """Stable digest of an insight's parameters (the brief has none: `{}`)."""
    return hashlib.sha256(json.dumps(params, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


EMPTY_PARAMS_HASH = params_hash({})

BRIEF_FACTS_CAPPED = "BRIEF_FACTS_CAPPED"
BRIEF_PARTIAL_BUDGET = "BRIEF_PARTIAL_BUDGET"
BRIEF_MERGE_FALLBACK = "BRIEF_MERGE_FALLBACK"


class FactOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: FactField
    value: str
    evidence: list[str]


class BriefOut(BaseModel):
    """What the extract call returns. Strict-mode compatible (see module docstring)."""

    model_config = ConfigDict(extra="forbid")
    document_type: str
    summary: str
    category: Category
    tags: list[str]
    suggested_questions: list[str]
    facts: list[FactOut]
    tone: Tone
    tone_note: str
    tone_evidence: list[str]


class MergeOut(BaseModel):
    """What the merge call returns: the document-level fields only. Facts never pass through the merge model,
    so their citations cannot be lost or altered there."""

    model_config = ConfigDict(extra="forbid")
    document_type: str
    summary: str
    category: Category
    tags: list[str]
    suggested_questions: list[str]
    tone: Tone
    tone_note: str


def brief_schema(evidence: EvidenceSet | None) -> type[BriefOut]:
    """`BriefOut` whose fact `evidence` items are an enum of the request's handles (same rule as
    `answer_schema`: above `MAX_ENUM_HANDLES` they stay plain strings and `citations.resolve` is the only
    guard)."""
    if evidence is None or not evidence.enum_ok or not evidence.handles:
        return BriefOut
    handle: Any = Literal[evidence.handles]
    fact: Any = create_model(
        "Fact",
        __base__=FactOut,
        evidence=(list[handle], ...),
    )
    return create_model(
        "BriefOut", __base__=BriefOut, facts=(list[fact], ...), tone_evidence=(list[handle], ...)
    )


# --- normalization (code, after parsing) ------------------------------------------------------------------


def _clean(s: str) -> str:
    return " ".join(s.split())


def _cut(s: str, n: int) -> str:
    s = _clean(s)
    if len(s) <= n:
        return s
    cut = s[: n - 1].rsplit(" ", 1)[0] if " " in s[: n - 1] else s[: n - 1]
    return cut.rstrip(" ,;:.") + "…"


def normalize_tags(tags: Sequence[str]) -> list[str]:
    """Lowercase, trimmed, 1-3 words, deduplicated, at most 8. A tag that is empty or too long is dropped."""
    out: list[str] = []
    for t in tags:
        tag = re.sub(r"[^\w\s&/+.-]", "", _clean(t).lower()).strip(" .-")
        if not tag or len(tag.split()) > MAX_TAG_WORDS or len(tag) > MAX_TAG_CHARS:
            continue
        if tag not in out:
            out.append(tag)
        if len(out) == MAX_TAGS:
            break
    return out


def normalize_questions(questions: Sequence[str]) -> list[str]:
    out: list[str] = []
    for q in questions:
        q = _cut(q, MAX_QUESTION_CHARS)
        if q and q.lower() not in (x.lower() for x in out):
            out.append(q)
        if len(out) == MAX_QUESTIONS:
            break
    return out


@dataclass(frozen=True)
class Normalized:
    document_type: str
    summary: str
    category: str
    tags: tuple[str, ...]
    suggested_questions: tuple[str, ...]
    facts: tuple[FactOut, ...]
    reasons: tuple[str, ...]
    tone: str = "neutral"
    tone_note: str = ""
    tone_evidence: tuple[str, ...] = ()  # handles, resolved by code


def normalize(out: BriefOut) -> Normalized:
    """Cap and clean a parsed `BriefOut`. More than 40 facts keeps the first 40 (`BRIEF_FACTS_CAPPED`)."""
    reasons: list[str] = []
    raw = list(out.facts)
    if len(raw) > MAX_FACTS:
        raw = raw[:MAX_FACTS]
        reasons.append(BRIEF_FACTS_CAPPED)
    facts = tuple(
        FactOut(field=f.field, value=_cut(f.value, MAX_FACT_VALUE_CHARS), evidence=list(f.evidence))
        for f in raw
        if _clean(f.value)
    )
    return Normalized(
        document_type=_cut(out.document_type, MAX_DOCUMENT_TYPE_CHARS),
        summary=_cut(out.summary, MAX_SUMMARY_CHARS),
        category=out.category,
        tags=tuple(normalize_tags(out.tags)),
        suggested_questions=tuple(normalize_questions(out.suggested_questions)),
        facts=facts,
        reasons=tuple(reasons),
        tone=out.tone,
        tone_note=_cut(out.tone_note, MAX_TONE_NOTE_CHARS),
        tone_evidence=tuple(dict.fromkeys(out.tone_evidence)),
    )


# --- resolved facts and the stored form -------------------------------------------------------------------


class StoredEvidence(BaseModel):
    """One resolved citation. `cited_text` is cut from the stored chunk, never taken from the model."""

    chunk_id: uuid.UUID
    version_id: uuid.UUID
    version_no: int
    pages: list[int]  # every page the chunk covers; empty for flow documents (DOCX/TXT/MD)
    section_path: list[str]
    sentence_start: int  # sentence indexes within the chunk, half-open
    sentence_end: int
    char_start: int
    char_end: int
    cited_text: str


class StoredFact(BaseModel):
    field: str
    value: str
    status: Literal["verified", "unverified"]
    reason: str | None  # NO_CITATION | VALUE_NOT_IN_CITATION when unverified
    evidence: list[StoredEvidence]


class StoredTone(BaseModel):
    """The document's overall tone. The label and note are the model's; `evidence` is resolved by code like a
    fact's. `unverified` (reason `NO_CITATION`) when no citation into this version resolved."""

    label: str
    note: str
    status: Literal["verified", "unverified"]
    reason: str | None
    evidence: list[StoredEvidence]


class Brief(BaseModel):
    """The `document_insights.content` of a brief."""

    document_type: str
    category: str
    tags: list[str]
    summary: str
    suggested_questions: list[str]
    facts: list[StoredFact]
    reasons: list[str]
    partial: bool
    tone: StoredTone | None = None  # absent in briefs stored before brief@2


@dataclass(frozen=True)
class ResolvedFact:
    field: str
    value: str
    evidence: tuple[StoredEvidence, ...]


def resolve_facts(
    facts: Sequence[FactOut], evidence: EvidenceSet
) -> tuple[list[ResolvedFact], tuple[str, ...]]:
    """Handles -> `StoredEvidence` through the chat resolver. Unknown handles are dropped
    (`CITATION_UNRESOLVED`); a fact left with no reference is kept and later marked `NO_CITATION`."""
    resolution = resolve([{"text": f.value, "evidence": f.evidence} for f in facts], evidence)
    out: list[ResolvedFact] = []
    for fact, seg in zip(facts, resolution.segments, strict=True):
        out.append(ResolvedFact(fact.field, fact.value, _stored(seg.references, evidence)))
    return out, resolution.reasons


def resolve_tone(
    note: str, handles: Sequence[str], evidence: EvidenceSet
) -> tuple[tuple[StoredEvidence, ...], tuple[str, ...]]:
    """Tone handles -> `StoredEvidence` (same resolver, same drop rule), at most `MAX_TONE_EVIDENCE`."""
    resolution = resolve([{"text": note, "evidence": list(handles)}], evidence)
    return _stored(resolution.segments[0].references, evidence)[:MAX_TONE_EVIDENCE], resolution.reasons


def _stored(refs: Sequence[Any], evidence: EvidenceSet) -> tuple[StoredEvidence, ...]:
    out: list[StoredEvidence] = []
    for r in refs:
        chunk = evidence.chunks[r.chunk_id]
        pages = list(range(chunk.page_start, chunk.page_end + 1)) if chunk.page_start else []
        out.append(
            StoredEvidence(
                chunk_id=r.chunk_id,
                version_id=r.version_id,
                version_no=chunk.version_no,
                pages=pages,
                section_path=list(chunk.section_path),
                sentence_start=r.sentence_start,
                sentence_end=r.sentence_end,
                char_start=r.char_start,
                char_end=r.char_end,
                cited_text=r.cited_text,
            )
        )
    return tuple(out)


def fact_key(field: str, value: str) -> tuple[str, str]:
    """Deduplication key: the field and the value lowercased with punctuation and spacing collapsed."""
    v = re.sub(r"(?<=\d),(?=\d{3})", "", value.lower())  # 1,250,000 == 1250000
    v = re.sub(r"[^\w$%.]+", " ", v)
    return field, " ".join(v.split()).strip(" .")


def dedupe_facts(facts: Sequence[ResolvedFact]) -> list[ResolvedFact]:
    """One fact per `(field, normalized value)`, in first-seen order. Citations of duplicates are united (same
    chunk and sentence range once, at most `MAX_FACT_EVIDENCE` kept); they all come from the map step."""
    order: list[tuple[str, str]] = []
    merged: dict[tuple[str, str], ResolvedFact] = {}
    for f in facts:
        key = fact_key(f.field, f.value)
        have = merged.get(key)
        if have is None:
            order.append(key)
            merged[key] = ResolvedFact(f.field, f.value, f.evidence[:MAX_FACT_EVIDENCE])
            continue
        seen = {(e.chunk_id, e.sentence_start, e.sentence_end) for e in have.evidence}
        extra = [e for e in f.evidence if (e.chunk_id, e.sentence_start, e.sentence_end) not in seen]
        merged[key] = ResolvedFact(have.field, have.value, (*have.evidence, *extra)[:MAX_FACT_EVIDENCE])
    return [merged[k] for k in order]
