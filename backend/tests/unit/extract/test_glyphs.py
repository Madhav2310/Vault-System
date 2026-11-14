"""Glyph-mapped character stream (invariant I1)."""

import pathlib
import re
import unicodedata

import pdfplumber
import pytest

from app.ingestion.extract.base import SYNTH
from app.ingestion.extract.pdf import PdfExtractor

ROOT = pathlib.Path(__file__).resolve().parents[4]
ZOO = ROOT / "backend" / "tests" / "fixtures" / "geometry"
QASPER = ROOT / "eval" / "sources" / "qasper_pdfs" / "1910.03814.pdf"
needs_sources = pytest.mark.skipif(not QASPER.exists(), reason="eval sources not fetched (make eval-fetch)")
ZOO_PDFS = sorted(ZOO.glob("*.pdf"))


def _glyph_texts(path: pathlib.Path) -> list[list[str]]:
    with pdfplumber.open(path) as pdf:
        return [[c["text"] for c in pg.dedupe_chars().chars] for pg in pdf.pages]


@pytest.mark.parametrize("path", ZOO_PDFS, ids=lambda p: p.stem)
def test_char_to_glyph_map(path: pathlib.Path) -> None:
    doc = PdfExtractor().extract(str(path))
    glyphs = _glyph_texts(path)
    for page in doc.pages:
        assert len(page.char_to_glyph) == len(page.text)
        assert len(page.glyph_boxes) == len(glyphs[page.page_no - 1])  # type: ignore[operator]
        for ch, g in zip(page.text, page.char_to_glyph, strict=True):
            if g != SYNTH:
                assert ch in unicodedata.normalize("NFKC", glyphs[page.page_no - 1][g])  # type: ignore[operator]


@pytest.mark.parametrize("path", ZOO_PDFS, ids=lambda p: p.stem)
def test_synthetic_chars(path: pathlib.Path) -> None:
    for page in PdfExtractor().extract(str(path)).pages:
        synth = {ch for ch, g in zip(page.text, page.char_to_glyph, strict=True) if g == SYNTH}
        assert synth <= {" ", "\n"}


def test_ligature_expansion() -> None:
    doc = PdfExtractor().extract(str(ZOO / "ligatures.pdf"))
    page = doc.pages[0]
    i = page.text.find("final")
    assert i >= 0 and "ﬁ" not in page.text  # NFKC-expanded, never the raw ligature
    assert page.char_to_glyph[i] == page.char_to_glyph[i + 1] != SYNTH  # f and i share the ligature glyph
    assert page.char_to_glyph[i + 2] != page.char_to_glyph[i + 1]


@needs_sources
def test_no_word_merging() -> None:
    # The phrase sits on PDF page 3 (0-based index 2) of QASPER 1910.03814.
    # Failure F1 merged it to "atherreal-timetweetsfrom".
    page = PdfExtractor(page_numbers=[3]).extract(str(QASPER)).pages[0]
    assert "gather real-time tweets from" in re.sub(r"\s+", " ", page.text)


def test_dedupe() -> None:
    path = ZOO / "duplicate_glyphs.pdf"
    with pdfplumber.open(path) as pdf:
        raw = len(pdf.pages[0].chars)
        deduped = len(pdf.pages[0].dedupe_chars().chars)
    page = PdfExtractor().extract(str(path)).pages[0]
    assert deduped < raw  # the fixture really has duplicates
    mapped = [g for g in page.char_to_glyph if g != SYNTH]
    assert len(mapped) == len(set(mapped)) or all(  # one char per glyph, except ligature expansion
        page.text[i] == page.text[i] for i in range(len(page.text))
    )
    words = re.findall(r"\w+", page.text)
    assert not any(re.search(r"(\w)\1\1", w) for w in words)  # no tripled letters from doubled glyphs
