"""`chunker@2` boundary rules (S22.01; design §6.5 successor, research parsing_chunking_indexing).

Differences from `chunker@1`, all about where chunks start and end and what the index sees:
- cut at headings, table boundaries and captions first, then pack by tokens (target 350, max 600);
- a table stays whole up to 1,200 tokens; a longer one splits into balanced row pieces and every piece after
  the first gets the header row in its *index text* only (`original_text` holds real page slices, I1);
- a row stays one sentence (handle); a figure/table caption is its own small chunk, linked to the table;
- `chunker@2-overlap25` adds the last 25% of the previous same-section text chunk's sentences to the index
  text only.

Evidence is untouched: `original_text`, `sentences` and `spans` come from `_assemble` over exact page slices.
Index-only additions live in `embedding_text` and `structural_prefix`, never in `original_text`.
"""

import hashlib
import math
import re
from collections.abc import Iterator, Sequence
from typing import Any, Literal

from app.ingestion.chunk import (
    ChunkDraft,
    Unit,
    _assemble,
    _prefix,
    _units,
    count_tokens,
)
from app.ingestion.extract.base import Element, ExtractedDocument

CHUNKER_V2 = "chunker@2"
CHUNKER_V2_OVERLAP = "chunker@2-overlap25"
V2_VERSIONS = (CHUNKER_V2, CHUNKER_V2_OVERLAP)
TARGET_TOKENS = 350
MAX_TOKENS = 600
TABLE_MAX_TOKENS = 1200
OVERLAP_RATIO = 0.25
JOIN = "\n\n"

_CAPTION = re.compile(
    r"^\s*(Table|Figure|Fig\.|Exhibit|Chart)\s+[A-Za-z]?\d+[A-Za-z]?(\.\d+)*\s*[.:\u2013\u2014-]"
)
_CAPTION_MAX_TOKENS = 80

_Draft = tuple[Literal["text", "table"], list[str], list[Unit], dict[str, Any]]


def is_caption(el: Element) -> bool:
    """A short paragraph that starts like "Table 3:" / "Figure 2." (a caption owns its own chunk)."""
    return (
        el.kind == "paragraph"
        and _CAPTION.match(el.text) is not None
        and count_tokens(el.text) <= _CAPTION_MAX_TOKENS
    )


def pack(units: Sequence[Unit]) -> Iterator[list[Unit]]:
    """Greedy whole-sentence packing to TARGET_TOKENS (never above MAX_TOKENS); no overlap."""
    i = 0
    while i < len(units):
        chunk: list[Unit] = []
        total = 0
        while i < len(units) and (not chunk or total + units[i].tokens <= TARGET_TOKENS):
            chunk.append(units[i])
            total += units[i].tokens
            i += 1
        rest = sum(u.tokens for u in units[i:])  # a small tail joins this chunk if the max allows
        if i < len(units) and rest < TARGET_TOKENS * 0.25 and total + rest <= MAX_TOKENS:
            chunk.extend(units[i:])
            i = len(units)
        yield chunk


def table_pieces(rows: list[Unit]) -> Iterator[list[Unit]]:
    """Whole table up to TABLE_MAX_TOKENS; otherwise balanced row pieces, each at most TABLE_MAX_TOKENS
    including the repeated header's tokens (the header is added to the index text by the caller)."""
    if not rows:
        return
    total = sum(u.tokens for u in rows)
    if total <= TABLE_MAX_TOKENS:
        yield list(rows)
        return
    header_cost = rows[0].tokens
    cap = TABLE_MAX_TOKENS - header_cost
    n = math.ceil(total / cap)
    budget = min(cap, math.ceil(total / n) + max(u.tokens for u in rows))
    piece: list[Unit] = []
    used = 0
    for r in rows:
        if piece and used + r.tokens > budget:
            yield piece
            piece, used = [], 0
        piece.append(r)
        used += r.tokens
    if piece:
        yield piece


def overlap_sentences(prev_text: str, prev_sentences: Sequence[tuple[int, int]]) -> str:
    """The last 25% (rounded up, at least one) of the previous chunk's sentences, joined by a space."""
    if not prev_sentences:
        return ""
    k = max(1, math.ceil(len(prev_sentences) * OVERLAP_RATIO))
    return " ".join(prev_text[s:e] for s, e in prev_sentences[-k:])


def _norm(text: str) -> str:
    return " ".join(text.split())


def chunk_document_v2(doc: ExtractedDocument, title: str, version: str = CHUNKER_V2) -> list[ChunkDraft]:
    if version not in V2_VERSIONS:
        raise ValueError(f"unknown chunker version {version!r}")
    drafts: list[_Draft] = []
    group: list[Unit] = []
    group_path: list[str] | None = None
    body_units = 0  # a heading with no body text is not a chunk: it lives on in its children's prefixes
    els = doc.elements

    def flush() -> None:
        nonlocal group, body_units
        if group and body_units:
            for packed in pack(group):
                drafts.append(("text", list(group_path or []), packed, {}))
        group, body_units = [], 0

    for i, el in enumerate(els):
        caption = is_caption(el)
        if el.kind == "heading":
            flush()
            group_path = [*el.section_path, _norm(el.text)]
            group = _units(doc, el, MAX_TOKENS, TARGET_TOKENS)  # the heading opens its section's first chunk
            continue
        if el.kind in ("table", "footnote") or caption:
            pending_path = group_path
            flush()
            group_path = pending_path
        if el.kind == "table":
            rows = _units(doc, el, TABLE_MAX_TOKENS, TABLE_MAX_TOKENS)
            cap_text = _table_caption(els, i)
            for n, piece in enumerate(table_pieces(rows)):
                extras: dict[str, Any] = {}
                if n > 0 and el.table_header:
                    extras["header"] = el.table_header
                if cap_text:
                    extras["table_caption"] = cap_text
                drafts.append(("table", list(el.section_path), piece, extras))
            continue
        if el.kind == "footnote":
            for packed in pack(_units(doc, el, MAX_TOKENS, TARGET_TOKENS)):
                drafts.append(("text", list(el.section_path), packed, {}))
            continue
        if caption:
            cap_units = _units(doc, el, MAX_TOKENS, TARGET_TOKENS)
            if cap_units:
                drafts.append(("text", list(el.section_path), cap_units, {"is_caption": True}))
            continue
        if group_path is None or el.section_path != group_path:
            flush()
            group_path = list(el.section_path)
        units = _units(doc, el, MAX_TOKENS, TARGET_TOKENS)
        group.extend(units)
        body_units += len(units)
    flush()
    return _finish(doc, title, drafts, version)


def _table_caption(els: Sequence[Element], i: int) -> str | None:
    """The caption element right before or right after table `i` that names a table."""
    for j in (i - 1, i + 1):
        if 0 <= j < len(els) and is_caption(els[j]) and els[j].text.lstrip().lower().startswith("table"):
            return _norm(els[j].text)
    return None


def _finish(doc: ExtractedDocument, title: str, drafts: list[_Draft], version: str) -> list[ChunkDraft]:
    out: list[ChunkDraft] = []
    for kind, path, units, extras in drafts:
        text, sentences, spans = _assemble(doc, units, "\n" if kind == "table" else JOIN)
        prefix = _prefix(title, path, spans)
        caption = extras.get("table_caption")
        if caption:
            prefix += f" | Table: {caption}"
        lines = [prefix]
        if extras.get("header"):
            lines.append(f"Header: {extras['header']}")  # index text only; cited row text stays untouched
        if version == CHUNKER_V2_OVERLAP and kind == "text" and not extras.get("is_caption") and out:
            prev = out[-1]
            if prev.kind == "text" and prev.section_path == path and not prev.extras.get("is_caption"):
                ctx = overlap_sentences(prev.original_text, prev.sentences)
                if ctx:
                    lines.append(f"Context: {ctx}")
        embedding_text = "\n".join([*lines, text])
        pages = [s.page for s in spans if s.page is not None]
        out.append(
            ChunkDraft(
                ordinal=len(out),
                kind=kind,
                original_text=text,
                structural_prefix=prefix,
                section_path=path,
                page_start=min(pages) if pages else 0,
                page_end=max(pages) if pages else 0,
                sentences=sentences,
                spans=spans,
                token_count=count_tokens(text),
                embedding_text=embedding_text,
                content_hash=hashlib.sha256((embedding_text + version).encode()).hexdigest(),
                extras=dict(extras),
            )
        )
    _link_captions(out)
    return out


def _link_captions(chunks: list[ChunkDraft]) -> None:
    """Link a caption chunk to its adjacent table chunk (extras only; not persisted, no schema change)."""
    for i, c in enumerate(chunks):
        if not c.extras.get("is_caption"):
            continue
        for j in (i + 1, i - 1):
            if (
                0 <= j < len(chunks)
                and chunks[j].kind == "table"
                and chunks[j].extras.get("table_caption") == _norm(c.original_text)
            ):
                c.extras["linked_table_ordinal"] = chunks[j].ordinal
                chunks[j].extras["caption_ordinal"] = c.ordinal
                break
