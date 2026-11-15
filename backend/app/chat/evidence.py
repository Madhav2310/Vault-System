"""Evidence set, citation handles, strict answer schema and manifest.

Each evidence chunk gets a request-scoped handle `E<k>` (k from 1) and each of its sentences `E<k>.<i>`
(i from 0). The prompt shows one line per sentence. The model answers with
`{status, segments:[{text, evidence:[handle]}]}`; in RAG mode every `evidence` item is an enum of the handles
present in the request, so the decoder cannot emit an unknown one. Above `MAX_ENUM_HANDLES` (the API allows
<= 1000 enum values) the items are plain strings and the code check in `citations.py` is the only guard.
Only `chunks.original_text` is evidence: prefixes are never shown.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model

from app.chat.nodes_common import defuse

MAX_ENUM_HANDLES = 1000
HEADER_SEP = " · "  # middle dot, as in the design's header format

Mode = Literal["rag", "full_context"]


class ConflictItem(BaseModel):
    """One conflict as the model writes it (part of the strict answer schema; see `conflicts.py`)."""

    model_config = ConfigDict(extra="forbid")
    topic: str
    handles_a: list[str]
    handles_b: list[str]


@dataclass(frozen=True)
class EvidenceChunk:
    """A retrieved chunk as stored (what retrieval returns; text is never altered)."""

    chunk_id: uuid.UUID
    version_id: uuid.UUID
    doc_title: str
    version_no: int
    page_start: int  # 0 for flow documents (DOCX/TXT/MD): no page
    page_end: int
    section_path: tuple[str, ...]
    original_text: str
    sentences: tuple[tuple[int, int], ...]  # half-open offsets into original_text
    document_id: uuid.UUID | None = None


@dataclass(frozen=True)
class ManifestEntry:
    chunk_id: uuid.UUID
    version_id: uuid.UUID
    sentence_index: int
    start: int  # offsets into the chunk's original_text
    end: int


@dataclass(frozen=True)
class EvidenceSet:
    text: str  # the `<evidence>` block for the prompt
    manifest: dict[str, ManifestEntry]  # handle -> entry
    chunks: dict[uuid.UUID, EvidenceChunk]
    handles: tuple[str, ...]  # every sentence handle, in prompt order
    chunk_handles: dict[uuid.UUID, str]  # chunk id -> E<k>

    @property
    def enum_ok(self) -> bool:
        return len(self.handles) <= MAX_ENUM_HANDLES


def header(k: int, c: EvidenceChunk) -> str:
    parts = [f"[E{k}] {defuse(c.doc_title)} v{c.version_no}"]
    if c.page_start:
        pages = f"p.{c.page_start}" + (f"-{c.page_end}" if c.page_end != c.page_start else "")
        parts.append(pages)
    section = defuse(" > ".join(" ".join(p.split()) for p in c.section_path))
    if section:
        parts.append(section)
    return HEADER_SEP.join(parts)


def _line(text: str) -> str:
    """One line per sentence, delimiter-like tags defused. Display only: `cited_text` comes from storage."""
    return defuse(" ".join(text.split()))


def build_evidence(chunks: Sequence[EvidenceChunk]) -> EvidenceSet:
    lines: list[str] = []
    manifest: dict[str, ManifestEntry] = {}
    handles: list[str] = []
    by_id: dict[uuid.UUID, EvidenceChunk] = {}
    chunk_handles: dict[uuid.UUID, str] = {}
    for k, c in enumerate(chunks, start=1):
        by_id[c.chunk_id] = c
        chunk_handles[c.chunk_id] = f"E{k}"
        lines.append(header(k, c))
        for i, (s, e) in enumerate(c.sentences):
            handle = f"E{k}.{i}"
            manifest[handle] = ManifestEntry(c.chunk_id, c.version_id, i, s, e)
            handles.append(handle)
            lines.append(f"{handle} {_line(c.original_text[s:e])}")
        lines.append("")
    body = "\n".join(lines).strip()
    return EvidenceSet(f"<evidence>\n{body}\n</evidence>", manifest, by_id, tuple(handles), chunk_handles)


def answer_schema(evidence: EvidenceSet | None, mode: Mode = "rag") -> type[BaseModel]:
    """Strict answer schema. RAG mode with an enum-sized evidence set: handle enum; else plain strings."""
    cfg = ConfigDict(extra="forbid")
    item: Any = str
    if mode == "rag" and evidence is not None and evidence.enum_ok and evidence.handles:
        item = Literal[evidence.handles]
    status: Any = Literal["answer", "abstain"]
    segment: Any = create_model("Segment", __config__=cfg, text=(str, ...), evidence=(list[item], ...))
    # `followups` comes last (the model writes segments first, so streaming is not delayed) and defaults to
    # empty for replies without it; on the wire the strict schema still requires it. Limits are code.
    return create_model(
        "Answer",
        __config__=cfg,
        status=(status, ...),
        segments=(list[segment], ...),
        followups=(list[str], Field(default_factory=list)),
        # handles are plain strings (an enum here would triple the schema's enum-value count);
        # `conflicts.resolve_conflicts` validates them against the manifest.
        conflicts=(list[ConflictItem], Field(default_factory=list)),
    )


def cache_mode_for(mode: Mode) -> Literal["implicit", "explicit"] | None:
    """OpenAI prompt-cache mode per answer mode. RAG evidence differs on every request,
    so caching it only adds the 1.25x write charge: `explicit` with no breakpoint disables it. FULL_CONTEXT
    keeps the default until the document-block breakpoint syntax is verified."""
    return "explicit" if mode == "rag" else None
