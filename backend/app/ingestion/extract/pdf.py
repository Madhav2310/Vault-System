"""PDF extractor (design §6.4, research 05 §3): pdfplumber glyphs → lines → elements over a glyph-mapped
character stream (invariant I1).

Per page: dedupe glyphs, extract words in the text's upright frame with the sweep-chosen config, group
them into lines, split lines into headings and paragraphs, then assemble the page text. Every page-text
char maps to a glyph (or -1 for a synthetic newline/space); element texts are exact slices of the page text.
"""

import re
import statistics
from dataclasses import dataclass
from typing import Any

import pdfplumber

from app.ingestion.extract.base import SYNTH, Element, ElementKind, ExtractedDocument, PageText
from app.ingestion.extract.glyphs import (
    Line,
    PageGeo,
    build_lines,
    extract_words,
    text_rotation,
    upright_chars,
)
from app.ingestion.extract.reading_order import order_page

HEADING_SIZE_RATIO = 1.15
# Structural headings (PART I, ITEM 1A., ARTICLE 5, SECTION 2.1, EXHIBIT A) or all-caps lines rank above
# mixed-case headings of the same size, so "Risk Factors Summary" nests under "ITEM 1A. RISK FACTORS".
_STRUCTURAL = re.compile(r"^(PART|ITEM|ARTICLE|SECTION|SCHEDULE|EXHIBIT|APPENDIX|ANNEX)\s+[\dIVXLA-Z]", re.I)
SHORT_LINE_CHARS = 80
PARA_GAP_RATIO = 0.8  # vertical gap above this times the line height starts a new paragraph


@dataclass
class _Block:
    kind: ElementKind
    lines: list[Line]
    level: int | None = None


@dataclass
class _Page:
    page_no: int
    width: float
    height: float
    lines: list[Line]
    footnotes: list[Line]
    boxes: list[tuple[float, float, float, float]]
    glyph_count: int
    flags: list[str]


def _body_size(pages: list[_Page]) -> float:
    sizes = [ln.size for p in pages for ln in p.lines for _ in range(len(ln.text))]
    return statistics.median(sizes) if sizes else 0.0


def _is_heading(ln: Line, body: float, page_width: float) -> bool:
    text = ln.text.strip()
    if not text or len(text) > 2 * SHORT_LINE_CHARS or not any(ch.isalpha() for ch in text):
        return False
    if body and ln.size > body * HEADING_SIZE_RATIO:
        return True
    return (
        ln.bold
        and len(text) <= SHORT_LINE_CHARS
        and not text.endswith((".", ",", ";", ":"))
        and (ln.x1 - ln.x0) < 0.75 * page_width
    )


def _structural(text: str) -> bool:
    t = text.strip()
    letters = [c for c in t if c.isalpha()]
    return bool(_STRUCTURAL.match(t)) or (len(letters) >= 4 and all(c.isupper() for c in letters))


def _blocks(lines: list[Line], body: float, page_width: float, levels: dict[float, int]) -> list[_Block]:
    heights = [ln.height for ln in lines if ln.height > 0]
    typical = statistics.median(heights) if heights else 10.0
    out: list[_Block] = []
    for ln in lines:
        if _is_heading(ln, body, page_width):
            level = 2 * levels.get(round(ln.size, 1), max(levels.values(), default=0) + 1) - 1
            if not _structural(ln.text):
                level += 1
            prev = out[-1] if out else None
            # consecutive heading lines of the same level are one wrapped heading
            if (
                prev
                and prev.kind == "heading"
                and prev.level == level
                and ln.top - prev.lines[-1].bottom < typical
            ):
                prev.lines.append(ln)
            else:
                out.append(_Block("heading", [ln], level))
            continue
        prev = out[-1] if out else None
        if prev and prev.kind == "paragraph":
            last = prev.lines[-1]
            gap = ln.top - last.bottom
            if (
                -typical < gap <= PARA_GAP_RATIO * max(last.height, typical)
                and abs(ln.size - last.size) < 1.0
            ):
                prev.lines.append(ln)
                continue
        out.append(_Block("paragraph", [ln]))
    return out


def _heading_levels(pages: list[_Page], body: float) -> dict[float, int]:
    """Larger heading font → lower level number; bold body-size headings get the next level."""
    sizes = sorted(
        {
            round(ln.size, 1)
            for p in pages
            for ln in p.lines
            if body and ln.size > body * HEADING_SIZE_RATIO and _is_heading(ln, body, p.width)
        },
        reverse=True,
    )
    return {s: i + 1 for i, s in enumerate(sizes[:3])} | {s: 3 for s in sizes[3:]}


class PdfExtractor:
    name = "pdfplumber@1"

    def __init__(self, page_numbers: list[int] | None = None) -> None:
        self.page_numbers = page_numbers  # 1-based subset (tests and diagnostics); None = every page

    def extract(self, path: str) -> ExtractedDocument:
        pages = self._read(path)
        body = _body_size(pages)
        levels = _heading_levels(pages, body)
        out_pages: list[PageText] = []
        elements: list[Element] = []
        sections: list[tuple[int, str]] = []
        for idx, p in enumerate(pages):
            text: list[str] = []
            src: list[int] = []
            blocks = _blocks(p.lines, body, p.width, levels) + [
                _Block("footnote", [ln]) for ln in p.footnotes
            ]
            for block in blocks:
                if text:
                    text.append("\n\n")
                    src.extend([SYNTH, SYNTH])
                start = sum(len(t) for t in text)
                for i, ln in enumerate(block.lines):
                    if i:
                        text.append("\n")
                        src.append(SYNTH)
                    text.append(ln.text)
                    src.extend(ln.src)
                end = sum(len(t) for t in text)
                body_text = "".join(text)[start:end]
                if block.kind == "heading":
                    level = block.level or 1
                    while sections and sections[-1][0] >= level:
                        sections.pop()
                    elements.append(
                        Element("heading", idx, start, end, body_text, [t for _, t in sections], level=level)
                    )
                    sections.append((level, " ".join(body_text.split())))
                else:
                    elements.append(Element(block.kind, idx, start, end, body_text, [t for _, t in sections]))
            page_text = "".join(text)
            out_pages.append(
                PageText(p.page_no, page_text, p.width, p.height, src, p.boxes, p.flags, p.glyph_count)
            )
        return ExtractedDocument(self.name, out_pages, elements)

    def _read(self, path: str) -> list[_Page]:
        out: list[_Page] = []
        with pdfplumber.open(path) as pdf:
            for i, page in enumerate(pdf.pages):
                if self.page_numbers is not None and i + 1 not in self.page_numbers:
                    continue
                out.append(self._page(page, i + 1))
                page.close()  # release pdfplumber's per-page caches (large documents)
        return out

    def _page(self, page: Any, page_no: int) -> _Page:
        geo = PageGeo.of(page)
        raw = page.dedupe_chars().chars
        chars = upright_chars(raw, page.width, page.height)
        rot = text_rotation(raw)
        uw, uh = (page.height, page.width) if rot in (90, 270) else (page.width, page.height)
        ordered = order_page(build_lines(extract_words(chars)), uw, uh)
        boxes = [geo.normalize((c["x0"], c["top"], c["x1"], c["bottom"])) for c in raw]
        w, h = geo.display_wh()
        return _Page(page_no, w, h, ordered.body, ordered.footnotes, boxes, len(raw), ordered.flags)
