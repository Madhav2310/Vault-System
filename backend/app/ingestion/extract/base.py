"""Extraction output (design §5.2, §6.4).

Every document becomes one or more `PageText`s plus `Element`s that point into them:
- PDF: one `PageText` per page; `text` is the glyph-mapped character stream (invariant I1): each char maps to
  a glyph index in `char_to_glyph`, or -1 for a synthetic separator (space, newline, table ` | `).
- DOCX/TXT/MD: one flow `PageText` with `page_no=None` and no glyphs.

`element.text == pages[i].text[element.start:element.end]` always holds; chunks are later cut from these
slices and never re-normalized.
"""

import gzip
import json
from dataclasses import asdict, dataclass, field
from typing import Literal, Protocol

ElementKind = Literal["heading", "paragraph", "list_item", "table", "footnote"]
SYNTH = -1
Box = tuple[float, float, float, float]  # normalized x0, top, x1, bottom in [0, 1] of the rendered page


@dataclass
class Element:
    kind: ElementKind
    page_index: int  # index into ExtractedDocument.pages
    start: int  # offsets into pages[page_index].text, half-open
    end: int
    text: str
    section_path: list[str] = field(default_factory=list)
    level: int | None = None  # heading level
    para_index: int | None = None  # DOCX/TXT/MD location (no pages)
    table_header: str | None = None  # tables: the header row text, repeated when the table is split/continued


@dataclass
class PageText:
    page_no: int | None  # 1-based for PDFs; None for flow documents
    text: str
    width: float = 0.0  # rendered-page size in points (PDF only)
    height: float = 0.0
    char_to_glyph: list[int] = field(default_factory=list)  # PDF only, len == len(text)
    glyph_boxes: list[Box] = field(default_factory=list)  # PDF only
    flags: list[str] = field(
        default_factory=list
    )  # extraction repairs and signals (e.g. reading_order_repaired)
    glyph_count: int = 0  # raw glyphs on the page before filtering (0 → image-only page)


@dataclass
class ExtractedDocument:
    extractor: str  # e.g. "pdfplumber@1"
    pages: list[PageText]
    elements: list[Element]
    replacement_chars: int = 0  # U+FFFD produced by decoding fallbacks

    def to_json_gz(self) -> bytes:
        return gzip.compress(json.dumps(asdict(self), ensure_ascii=False, separators=(",", ":")).encode(), 6)

    @classmethod
    def from_json_gz(cls, data: bytes) -> "ExtractedDocument":
        raw = json.loads(gzip.decompress(data))
        pages = [PageText(**{**p, "glyph_boxes": [tuple(b) for b in p["glyph_boxes"]]}) for p in raw["pages"]]
        return cls(raw["extractor"], pages, [Element(**e) for e in raw["elements"]], raw["replacement_chars"])

    def check_offsets(self) -> None:
        """Invariant: every element is an exact slice of its page text (raises AssertionError otherwise)."""
        for e in self.elements:
            got = self.pages[e.page_index].text[e.start : e.end]
            if got != e.text:
                raise AssertionError(f"element offsets drifted on page index {e.page_index} at {e.start}")


class Extractor(Protocol):
    name: str

    def extract(self, path: str) -> ExtractedDocument: ...
