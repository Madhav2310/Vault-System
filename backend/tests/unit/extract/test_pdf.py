"""PDF elements: headings and offset consistency."""

import pathlib

import pytest

from app.ingestion.extract.pdf import PdfExtractor

ROOT = pathlib.Path(__file__).resolve().parents[4]
AMD = ROOT / "eval" / "sources" / "fb_pdfs" / "AMD_2022_10K.pdf"
ZOO = ROOT / "backend" / "tests" / "fixtures" / "geometry"
SOURCES = ROOT / "eval" / "sources"


@pytest.mark.skipif(not AMD.exists(), reason="eval sources not fetched (make eval-fetch)")
def test_headings_detected() -> None:
    doc = PdfExtractor(page_numbers=list(range(15, 20))).extract(str(AMD))
    headings = {
        (doc.pages[e.page_index].page_no, " ".join(e.text.split()))
        for e in doc.elements
        if e.kind == "heading"
    }
    assert (17, "ITEM 1A. RISK FACTORS") in headings
    after = [e for e in doc.elements if doc.pages[e.page_index].page_no == 18 and e.kind == "paragraph"]
    assert after and any("ITEM 1A. RISK FACTORS" in " > ".join(e.section_path).upper() for e in after)


def test_offsets_consistent() -> None:
    paths = sorted(ZOO.glob("*.pdf"))
    paths += sorted((SOURCES / "qasper_pdfs").glob("*.pdf"))[:2] if SOURCES.exists() else []
    assert paths
    for p in paths:
        doc = PdfExtractor().extract(str(p))
        doc.check_offsets()
        for e in doc.elements:
            assert doc.pages[e.page_index].text[e.start : e.end] == e.text
