"""Chunker-independent gold: map gold evidence to chunks and sentence ranges at run time.

Gold evidence is a document-level sentence range; the runner joins those sentences into a quote and this
module locates the quote inside the ingested chunks of that document (`map_quote`), so gold survives a change
of chunker. Text is compared normalized (NFKC, curly quotes and dashes folded, whitespace collapsed,
lowercase; quote reference markers removed; quotes over 400 characters matched by a 300-character head and
tail), with a fuzzy fallback (`partial_ratio >= FUZZ_MIN`). Table cell separators (` | `) are synthetic
formatting, so they are dropped from chunk text before matching.

A chunk is normalized sentence by sentence (the chunk's `sentences` offsets) and the pieces are joined by one
space, so a hit position maps back to a half-open sentence range `[start, end)`, the same unit citations use.

Strategy per quote: exact normalized substring first (word-bounded when possible, restricted to chunks on the
given page when any exist there); only when no exact hit exists anywhere in the document, the best fuzzy
chunk(s). A very short quote may legitimately hit several chunks: all of them count as acceptable evidence.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Literal

from rapidfuzz import fuzz

__all__ = ["FUZZ_MIN", "REF", "ChunkHit", "ChunkInfo", "GoldTarget", "map_quote", "norm"]

REF = re.compile(r"\s*(BIBREF|TABREF|FIGREF|SECREF|INLINEFORM|DISPLAYFORM)\d*")  # QASPER reference markers
FUZZ_MIN = 88  # partial_ratio threshold for a located quote


def norm(s: str) -> str:
    """NFKC, curly quotes and dashes folded, soft hyphens dropped, whitespace collapsed, lowercase."""
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("\u2019", "'").replace("\u2018", "'").replace("\u201c", '"').replace("\u201d", '"')
    s = s.replace("\u2013", "-").replace("\u2014", "-").replace("\u00ad", "")
    return re.sub(r"\s+", " ", s).strip().lower()


_LONG = 400  # quotes longer than this are matched by a 300-char head and tail
_HEAD = 300
_TIE = 1.0  # fuzzy hits within this many points of the best count as ties


@dataclass(frozen=True)
class ChunkInfo:
    """What mapping needs from a stored chunk (`chunks` row)."""

    id: str
    doc_id: str  # eval document key
    ordinal: int
    text: str  # original_text
    sentences: tuple[tuple[int, int], ...]  # char offsets into `text`, as stored
    page_start: int
    page_end: int  # page_start == page_end == 0 for flow documents
    kind: str = "text"


@dataclass(frozen=True)
class ChunkHit:
    chunk_id: str
    sentence_start: int  # half-open sentence indexes within the chunk
    sentence_end: int
    match: Literal["exact", "fuzzy"]
    score: float
    on_page: bool


@dataclass(frozen=True)
class GoldTarget:
    """One gold quote resolved against the ingested chunks. No hits = unmapped (a harness problem)."""

    item_id: str
    doc_id: str
    quote: str
    page: int | None
    hits: tuple[ChunkHit, ...]
    reason: str | None = None  # why it is unmapped

    @property
    def mapped(self) -> bool:
        return bool(self.hits)

    @property
    def chunk_ids(self) -> frozenset[str]:
        return frozenset(h.chunk_id for h in self.hits)


@dataclass
class _Norm:
    text: str
    sent: list[tuple[int, int]] = field(default_factory=list)  # sentence idx -> [start, end) in `text`


def _clean(s: str) -> str:
    return norm(s.replace(" | ", " ").replace("| ", " ").replace(" |", " "))


@lru_cache(maxsize=8192)
def _normalize_chunk(c: ChunkInfo) -> _Norm:
    spans = list(c.sentences) or [(0, len(c.text))]
    out = _Norm("")
    parts: list[str] = []
    pos = 0
    for a, b in spans:
        piece = _clean(c.text[a:b])
        if parts:
            pos += 1  # the single joining space
        out.sent.append((pos, pos + len(piece)))
        parts.append(piece)
        pos += len(piece)
    out.text = " ".join(parts)
    return out


def _sent_range(n: _Norm, a: int, b: int) -> tuple[int, int]:
    """Half-open sentence range whose normalized spans overlap [a, b)."""
    idx = [i for i, (s, e) in enumerate(n.sent) if s < b and e > a]
    if not idx:  # hit entirely in a joining space: attribute to the nearest sentence
        idx = [min(range(len(n.sent)), key=lambda i: abs(n.sent[i][0] - a))]
    return idx[0], idx[-1] + 1


def _probes(quote: str) -> list[str]:
    q = norm(REF.sub("", quote))
    if len(q) > _LONG:
        return [q[:_HEAD], q[-_HEAD:]]
    return [q] if q else []


def _find_exact(q: str, text: str) -> list[tuple[int, int]]:
    """All occurrences; word-bounded ones when any exist (so 'reit' does not hit 'reits')."""
    spans = [(m.start(), m.end()) for m in re.finditer(re.escape(q), text)]
    bounded = [
        (a, b)
        for a, b in spans
        if (a == 0 or not _word(q[0]) or not _word(text[a - 1]))
        and (b == len(text) or not _word(q[-1]) or not _word(text[b]))
    ]
    return bounded or spans


def _word(ch: str) -> bool:
    return ch.isalnum() or ch == "_"


def map_quote(
    quote: str, page: int | None, chunks: Sequence[ChunkInfo], *, item_id: str = "", doc_id: str = ""
) -> GoldTarget:
    """Locate one quote among the chunks of ONE document."""
    probes = _probes(quote)
    if not probes:
        return GoldTarget(item_id, doc_id, quote, page, (), "empty quote")
    normed = {c.id: _normalize_chunk(c) for c in chunks}

    def on_page(c: ChunkInfo) -> bool:
        return page is None or c.page_start <= page <= c.page_end or (c.page_start == 0 and c.page_end == 0)

    exact: list[ChunkHit] = []
    for c in chunks:
        n = normed[c.id]
        ranges = [_find_exact(p, n.text) for p in probes]
        if not any(ranges):
            continue
        flat = [r for rs in ranges for r in rs]
        lo, hi = _sent_range(n, min(a for a, _ in flat), max(b for _, b in flat))
        exact.append(ChunkHit(c.id, lo, hi, "exact", 100.0, on_page(c)))
    if exact:
        here = [h for h in exact if h.on_page]
        return GoldTarget(item_id, doc_id, quote, page, tuple(here or exact))

    scored: list[tuple[float, ChunkInfo, int, int]] = []
    for c in chunks:
        n = normed[c.id]
        best = None
        for p in probes:
            al = fuzz.partial_ratio_alignment(p, n.text)
            if al is not None and (best is None or al.score > best.score):
                best = al
        if best is not None and best.score >= FUZZ_MIN:
            lo, hi = _sent_range(n, best.dest_start, best.dest_end)
            scored.append((float(best.score), c, lo, hi))
    if not scored:
        return GoldTarget(item_id, doc_id, quote, page, (), "no exact or fuzzy hit in any chunk")
    top = max(s for s, *_ in scored)
    hits = [
        ChunkHit(c.id, lo, hi, "fuzzy", round(s, 1), on_page(c)) for s, c, lo, hi in scored if s >= top - _TIE
    ]
    here = [h for h in hits if h.on_page]
    return GoldTarget(item_id, doc_id, quote, page, tuple(here or hits))


def chunks_from_rows(rows: Iterable[Mapping[str, Any]], doc_key_by_id: Mapping[str, str]) -> list[ChunkInfo]:
    """`SELECT id, document_id, ordinal, original_text, sentences, page_start, page_end, kind FROM chunks`."""
    return [
        ChunkInfo(
            id=str(r["id"]),
            doc_id=doc_key_by_id[str(r["document_id"])],
            ordinal=r["ordinal"],
            text=r["original_text"],
            sentences=tuple((int(a), int(b)) for a, b in r["sentences"]),
            page_start=r["page_start"],
            page_end=r["page_end"],
            kind=r["kind"],
        )
        for r in rows
    ]
