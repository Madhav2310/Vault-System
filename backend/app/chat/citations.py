"""Citation resolver (design §8.5). Code, not the model, decides what a citation is.

Every handle a segment names is looked up in the request's manifest. Unknown handles are dropped
(`CITATION_UNRESOLVED`, metric `vault.citations.unresolved`). Adjacent sentences of one chunk merge into one
half-open sentence range, and `cited_text` is cut from the stored `original_text`, never taken from the model.
A segment with no valid handle is kept and marked `support=none` (shown as uncited), never silently dropped.
"""

import uuid
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from opentelemetry import metrics

from app.chat.evidence import EvidenceSet

CITATION_UNRESOLVED = "CITATION_UNRESOLVED"
_unresolved = metrics.get_meter("vault.chat").create_counter(
    "vault.citations.unresolved",
    description="Citation handles the model returned that the manifest does not know",
)


@dataclass(frozen=True)
class EvidenceReference:
    chunk_id: uuid.UUID
    version_id: uuid.UUID
    sentence_start: int  # sentence indexes within the chunk, half-open
    sentence_end: int
    char_start: int  # offsets into the chunk's original_text
    char_end: int
    cited_text: str


@dataclass(frozen=True)
class ResolvedSegment:
    ordinal: int
    text: str
    support: str  # "cited" (has >= 1 valid reference) | "none"
    references: tuple[EvidenceReference, ...]
    dropped_handles: tuple[str, ...] = ()


@dataclass(frozen=True)
class Resolution:
    segments: tuple[ResolvedSegment, ...]
    reasons: tuple[str, ...] = field(default=())
    unresolved_count: int = 0


def _ranges(indexes: Sequence[int]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for i in sorted(set(indexes)):
        if out and out[-1][1] == i:
            out[-1] = (out[-1][0], i + 1)
        else:
            out.append((i, i + 1))
    return out


def resolve_segment(n: int, seg: Any, evidence: EvidenceSet) -> ResolvedSegment:
    """Resolve one segment (object or dict with `text` and `evidence` handles); no metric side effect."""
    text = seg["text"] if isinstance(seg, dict) else seg.text
    handles = list(seg["evidence"] if isinstance(seg, dict) else seg.evidence)
    valid: dict[uuid.UUID, list[int]] = defaultdict(list)
    dropped: list[str] = []
    for h in dict.fromkeys(handles):  # a repeated handle counts once
        entry = evidence.manifest.get(h)
        if entry is None:
            dropped.append(h)
        else:
            valid[entry.chunk_id].append(entry.sentence_index)
    refs: list[EvidenceReference] = []
    for chunk_id, idxs in valid.items():
        chunk = evidence.chunks[chunk_id]
        for a, b in _ranges(idxs):
            start, end = chunk.sentences[a][0], chunk.sentences[b - 1][1]
            refs.append(
                EvidenceReference(
                    chunk_id, chunk.version_id, a, b, start, end, chunk.original_text[start:end]
                )
            )
    return ResolvedSegment(n, text, "cited" if refs else "none", tuple(refs), tuple(dropped))


def resolve(segments: Sequence[Any], evidence: EvidenceSet) -> Resolution:
    """`segments`: objects or dicts with `.text` and `.evidence` (list of handle strings)."""
    resolved = [resolve_segment(n, seg, evidence) for n, seg in enumerate(segments)]
    dropped_total = sum(len(r.dropped_handles) for r in resolved)
    if dropped_total:
        _unresolved.add(dropped_total)
    return Resolution(tuple(resolved), (CITATION_UNRESOLVED,) if dropped_total else (), dropped_total)
