"""Page quality and flags (design §6.2, §6.3; S1.19).

Per PDF page: `text_chars` (non-whitespace), `quality_score` in [0, 1], and flags: the extractor's repairs and
signals (`multi_column`, `reading_order_repaired`, `footnotes_separated`, `has_tables`, `gutter_split`) plus
`needs_ocr` (< 50 text chars) and `garbled` (replacement or `(cid:n)` glyphs > 5% of the text). The version
becomes `needs_ocr` when at least 80% of its pages need OCR.
"""

from dataclasses import dataclass, field
from typing import Any

from app.ingestion.extract.base import ExtractedDocument

NEEDS_OCR_CHARS = 50
GARBLED_RATIO = 0.05
NEEDS_OCR_SHARE = 0.8


@dataclass(frozen=True)
class PageQuality:
    page_no: int
    width: float
    height: float
    text_chars: int
    quality_score: float
    flags: list[str] = field(default_factory=list)


def assess(doc: ExtractedDocument) -> tuple[list[PageQuality], dict[str, Any], bool]:
    """(page rows, version quality summary, version needs OCR)."""
    rows: list[PageQuality] = []
    for p in doc.pages:
        if p.page_no is None:
            continue
        chars = sum(1 for ch in p.text if not ch.isspace())
        bad = p.text.count("�") + 5 * p.text.count("(cid:")
        garbled = bad / max(chars, 1)
        flags = list(p.flags)
        if chars < NEEDS_OCR_CHARS:
            flags.append("needs_ocr")
        if garbled > GARBLED_RATIO:
            flags.append("garbled")
        score = round(min(1.0, chars / 200) * (1.0 - min(garbled, 1.0)), 3)
        rows.append(PageQuality(p.page_no, p.width, p.height, chars, score, flags))
    ocr = sum("needs_ocr" in r.flags for r in rows)
    needs_ocr = bool(rows) and ocr >= NEEDS_OCR_SHARE * len(rows)
    counts: dict[str, int] = {}
    for r in rows:
        for f in r.flags:
            counts[f] = counts.get(f, 0) + 1
    summary = {
        "pages": len(rows),
        "needs_ocr_pages": ocr,
        "mean_quality": round(sum(r.quality_score for r in rows) / len(rows), 3) if rows else None,
        "flags": counts,
        "replacement_chars": doc.replacement_chars,
        "extractor": doc.extractor,
    }
    return rows, summary, needs_ocr
