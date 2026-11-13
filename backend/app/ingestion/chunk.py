"""Structure-aware chunker `chunker@1` (design §6.5; research 05 §3.4).

Units are sentences (from `split_sentences`) located in page text. Within one section group, sentences are
packed up to ~400 tokens (hard max 800) with ~15% overlap made of whole sentences. Tables and footnotes are
standalone chunks; a table over the max splits by rows and repeats its header row in every piece (also for
a table continued from the previous page).

A chunk's `original_text` is made of exact page-text slices ("pieces"); adjacent pieces are joined by the
page text between them when that gap is whitespace, otherwise by a synthetic "\\n\\n". `spans` map every
piece back to (page, page_start, page_end), so the text never needs re-normalizing (invariant I1).
"""

import hashlib
import statistics
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Literal

import tiktoken

from app.ingestion.extract.base import Element, ExtractedDocument
from app.ingestion.sentences import split_sentences

CHUNKER_VERSION = "chunker@1"
TARGET_TOKENS = 400
MAX_TOKENS = 800
OVERLAP_RATIO = 0.15
JOIN = "\n\n"


@lru_cache(maxsize=1)
def _encoding() -> tiktoken.Encoding:
    return tiktoken.get_encoding("cl100k_base")  # the embedding model's tokenizer


def count_tokens(text: str) -> int:
    return len(_encoding().encode(text, disallowed_special=()))


@dataclass(frozen=True)
class Unit:
    """One sentence (or table row) located in the extracted document."""

    page_index: int
    start: int
    end: int
    tokens: int


@dataclass(frozen=True)
class PageSpan:
    start: int  # chunk offsets, half-open
    end: int
    page: int | None  # 1-based page number; None for flow documents (DOCX/TXT/MD)
    page_index: int
    page_start: int  # offsets into that page's text
    page_end: int


@dataclass
class ChunkDraft:
    ordinal: int
    kind: Literal["text", "table"]
    original_text: str
    structural_prefix: str
    section_path: list[str]
    page_start: int  # 0 for flow documents (no pages); the column is NOT NULL
    page_end: int
    sentences: list[tuple[int, int]]
    spans: list[PageSpan]
    token_count: int
    embedding_text: str = ""
    content_hash: str = ""
    extras: dict[str, Any] = field(default_factory=dict)


def _units(doc: ExtractedDocument, el: Element) -> list[Unit]:
    text = doc.pages[el.page_index].text
    kind: Literal["text", "table"] = "table" if el.kind == "table" else "text"
    out: list[Unit] = []
    for s, e in split_sentences(text[el.start : el.end], kind=kind):
        a, b = el.start + s, el.start + e
        tokens = count_tokens(text[a:b])
        if tokens <= MAX_TOKENS:
            out.append(Unit(el.page_index, a, b, tokens))
            continue
        out.extend(_hard_split(doc, el.page_index, a, b))  # a run-on "sentence" (e.g. an unpunctuated list)
    return out


def _hard_split(doc: ExtractedDocument, page_index: int, a: int, b: int) -> Iterator[Unit]:
    """Split an over-long sentence at whitespace into pieces of at most TARGET_TOKENS."""
    text = doc.pages[page_index].text
    start = a
    while start < b:
        end = start
        last_ws = None
        while end < b and count_tokens(text[start:end]) < TARGET_TOKENS:
            nxt = text.find(" ", end + 1, b)
            nl = text.find("\n", end + 1, b)
            cands = [x for x in (nxt, nl) if x != -1]
            if not cands:
                end = b
                break
            last_ws, end = end, min(cands)
        if end < b and last_ws is not None and last_ws > start:
            end = last_ws
        while start < end and text[start].isspace():
            start += 1
        piece_end = end
        while piece_end > start and text[piece_end - 1].isspace():
            piece_end -= 1
        if piece_end > start:
            yield Unit(page_index, start, piece_end, count_tokens(text[start:piece_end]))
        start = max(end, start + 1)


def _assemble(
    doc: ExtractedDocument, units: Sequence[Unit], join: str = JOIN
) -> tuple[str, list[tuple[int, int]], list[PageSpan]]:
    """Chunk text from units: contiguous runs on one page become one piece; returns text, sentences, spans."""
    parts: list[str] = []
    sentences: list[tuple[int, int]] = []
    spans: list[PageSpan] = []
    pos = 0
    run_start: Unit | None = None
    run_end: Unit | None = None
    run_chunk_start = 0

    def close_run() -> None:
        if run_start is not None and run_end is not None:
            page = doc.pages[run_start.page_index]
            spans.append(
                PageSpan(
                    run_chunk_start, pos, page.page_no, run_start.page_index, run_start.start, run_end.end
                )
            )

    for u in units:
        page_text = doc.pages[u.page_index].text
        if run_end is not None and run_end.page_index == u.page_index and u.start >= run_end.end:
            gap = page_text[run_end.end : u.start]
            if gap.strip() == "":  # contiguous in the page: keep the real separator, extend the piece
                parts.append(gap)
                pos += len(gap)
                s0 = pos
                parts.append(page_text[u.start : u.end])
                pos += u.end - u.start
                sentences.append((s0, pos))
                run_end = u
                continue
        close_run()
        if parts:
            parts.append(join)
            pos += len(join)
        run_start, run_end, run_chunk_start = u, u, pos
        parts.append(page_text[u.start : u.end])
        pos += u.end - u.start
        sentences.append((run_chunk_start, pos))
    close_run()
    return "".join(parts), sentences, spans


def _prefix(title: str, section: Sequence[str], spans: Sequence[PageSpan]) -> str:
    pages = sorted({s.page for s in spans if s.page is not None})
    sect = " > ".join(" ".join(p.split()) for p in section) or "-"
    out = f"Document: {title} | Section: {sect}"
    if pages:
        out += f" | Page: {pages[0]}" + (f"-{pages[-1]}" if pages[-1] != pages[0] else "")
    return out


def _pack(units: Sequence[Unit]) -> Iterator[list[Unit]]:
    """Greedy packing to TARGET_TOKENS (never above MAX_TOKENS) with whole-sentence overlap."""
    i = 0
    overlap_budget = int(TARGET_TOKENS * OVERLAP_RATIO)
    while i < len(units):
        chunk: list[Unit] = []
        total = 0
        j = i
        while j < len(units) and (not chunk or total + units[j].tokens <= TARGET_TOKENS):
            chunk.append(units[j])
            total += units[j].tokens
            j += 1
        # a small remainder joins this chunk if the max allows (avoids tiny trailing chunks)
        rest = sum(u.tokens for u in units[j:])
        if j < len(units) and rest < TARGET_TOKENS * 0.25 and total + rest <= MAX_TOKENS:
            chunk.extend(units[j:])
            j = len(units)
        yield chunk
        if j >= len(units):
            return
        # next chunk starts with the last whole sentences of this one, within the overlap budget
        back, used = j, 0
        while back - 1 > i and used + units[back - 1].tokens <= overlap_budget:
            back -= 1
            used += units[back].tokens
        i = back if back > i else j


def _table_pieces(
    doc: ExtractedDocument, el: Element, header: Unit | None, rows: list[Unit]
) -> Iterator[list[Unit]]:
    if not rows:
        return
    body = rows[1:] if header is not None and rows[0] == header else rows
    if sum(u.tokens for u in rows) <= MAX_TOKENS:
        yield ([header] if header is not None and rows[0] != header else []) + rows
        return
    budget = MAX_TOKENS - (header.tokens if header else 0)
    piece: list[Unit] = []
    total = 0
    for r in body:
        if piece and total + r.tokens > budget:
            yield ([header] if header else []) + piece
            piece, total = [], 0
        piece.append(r)
        total += r.tokens
    if piece:
        yield ([header] if header else []) + piece


def _header_unit(
    doc: ExtractedDocument, el: Element, rows: list[Unit], prior_tables: list[Element]
) -> Unit | None:
    """The header row as a located unit: this table's first row, or the matching row of an earlier table
    (a table continued from the previous page)."""
    if not el.table_header:
        return None
    page_text = doc.pages[el.page_index].text
    if rows and page_text[rows[0].start : rows[0].end] == el.table_header:
        return rows[0]
    for prev in reversed(prior_tables):
        ptext = doc.pages[prev.page_index].text
        if ptext[prev.start : prev.end].startswith(el.table_header):
            return Unit(
                prev.page_index, prev.start, prev.start + len(el.table_header), count_tokens(el.table_header)
            )
    return None


def chunk_document(doc: ExtractedDocument, title: str) -> list[ChunkDraft]:
    drafts: list[tuple[Literal["text", "table"], list[str], list[Unit]]] = []
    group: list[Unit] = []
    group_path: list[str] | None = None
    tables: list[Element] = []
    body_units = 0  # a heading with no body text is not a chunk: it lives on in its children's prefixes

    def flush() -> None:
        nonlocal group, body_units
        if group and body_units:
            for packed in _pack(group):
                drafts.append(("text", list(group_path or []), packed))
        group, body_units = [], 0

    for el in doc.elements:
        if el.kind == "heading":
            flush()
            group_path = [*el.section_path, " ".join(el.text.split())]
            group = _units(doc, el)  # the heading opens its section's first chunk
            continue
        if el.kind in ("table", "footnote"):
            # keep document order: emit the text before it first; text after it starts a new group
            pending_path = group_path
            flush()
            group_path = pending_path
        if el.kind == "table":
            rows = _units(doc, el)
            header = _header_unit(doc, el, rows, tables)
            for piece in _table_pieces(doc, el, header, rows):
                drafts.append(("table", list(el.section_path), piece))
            tables.append(el)
            continue
        if el.kind == "footnote":
            for packed in _pack(_units(doc, el)):
                drafts.append(("text", list(el.section_path), packed))
            continue
        if group_path is None or el.section_path != group_path:
            flush()
            group_path = list(el.section_path)
        units = _units(doc, el)
        group.extend(units)
        body_units += len(units)
    flush()

    out: list[ChunkDraft] = []
    for kind, path, units in drafts:
        text, sentences, spans = _assemble(doc, units, "\n" if kind == "table" else JOIN)
        pages = [s.page for s in spans if s.page is not None]
        prefix = _prefix(title, path, spans)
        embedding_text = prefix + "\n" + text
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
                content_hash=hashlib.sha256((embedding_text + CHUNKER_VERSION).encode()).hexdigest(),
            )
        )
    return out


def median_tokens(chunks: Sequence[ChunkDraft], kind: str = "text") -> float:
    sizes = [c.token_count for c in chunks if c.kind == kind]
    return statistics.median(sizes) if sizes else 0.0
