"""Reading-order safety: column repair and footnote separation."""

import pathlib
import re

from app.ingestion.extract.pdf import PdfExtractor
from tests.fixtures.make_fixtures import Page, pdf_bytes

ZOO = pathlib.Path(__file__).resolve().parents[2] / "fixtures" / "geometry"
LEFT = [f"Left column sentence number {i} about apples." for i in range(1, 9)]
RIGHT = [f"Right column sentence number {i} about pears." for i in range(1, 9)]


def _pdf(tmp: pathlib.Path, page: Page, name: str) -> str:
    p = tmp / name
    p.write_bytes(pdf_bytes([page]))
    return str(p)


def test_no_column_mixing(tmp_path: pathlib.Path) -> None:
    page = Page()
    for i, (lt, rt) in enumerate(zip(LEFT, RIGHT, strict=True)):  # drawn in step: L1 R1 L2 R2 ...
        page.text(lt, 72, 700 - 14 * i, 10)
        page.text(rt, 330, 700 - 14 * i, 10)
    doc = PdfExtractor().extract(_pdf(tmp_path, page, "interleaved.pdf"))
    text = doc.pages[0].text
    for line in text.split("\n"):
        assert not ("apples" in line and "pears" in line), line  # no line mixes the two columns
    body = re.sub(r"\s+", " ", text)
    assert body.index(LEFT[-1]) < body.index(RIGHT[0])  # the left column is read completely first
    assert "reading_order_repaired" in doc.pages[0].flags


def test_single_column_untouched(tmp_path: pathlib.Path) -> None:
    page = Page()
    lines = [
        "1. Definitions. In this Agreement the following terms apply:",
        "    (a) Services means the consulting services in Schedule A;",
        "    (b) Fees means the amounts in Schedule B.",
        "2. Term. The Agreement runs for twelve months from the Effective",
        "Date and renews unless either party gives notice.",
        "    (a) Notice must be in writing;",
        "    (b) Notice is effective on receipt.",
        "3. Law. Virginia law governs this Agreement.",
    ]
    for i, ln in enumerate(lines):
        indent = 30 if ln.startswith("    ") else 0
        page.text(ln.strip(), 72 + indent, 700 - 14 * i, 10)
    doc = PdfExtractor().extract(_pdf(tmp_path, page, "single.pdf"))
    got = [ln for ln in doc.pages[0].text.split("\n") if ln.strip()]
    assert got == [ln.strip() for ln in lines]  # same order, nothing split or moved
    assert not {"reading_order_repaired", "multi_column"} & set(doc.pages[0].flags)


def test_footnote_separated() -> None:
    doc = PdfExtractor().extract(str(ZOO / "footnote_two_column.pdf"))
    url = "https://github.com/example/vault-fixtures"
    body = [e for e in doc.elements if e.kind != "footnote"]
    assert not any(url in e.text for e in body)
    (fn,) = [e for e in doc.elements if e.kind == "footnote"]
    assert url in fn.text
    sentence = "We instead annotate every tweet with fine-grained labels from experts."
    assert sentence in " ".join(" ".join(e.text.split()) for e in body)
