"""PDF tables: glyph-mapped pipe tables, no duplicated text, header carried across pages."""

import pathlib

import pytest

from app.ingestion.chunk import chunk_document
from app.ingestion.extract.base import SYNTH
from app.ingestion.extract.pdf import PdfExtractor
from tests.fixtures.make_fixtures import Page, pdf_bytes

ROOT = pathlib.Path(__file__).resolve().parents[4]
AMD = ROOT / "eval" / "sources" / "fb_pdfs" / "AMD_2022_10K.pdf"
SNAPSHOT = ROOT / "backend" / "tests" / "fixtures" / "snapshots" / "amd_10k_p48_table.txt"


def ruled_table(
    page: Page, rows: list[list[str]], x: float, top_y: float, col_w: float = 150, row_h: float = 18
) -> float:
    """Draw a ruled grid with text; returns the y below the table."""
    n_rows, n_cols = len(rows), len(rows[0])
    bottom = top_y - n_rows * row_h
    for r in range(n_rows + 1):
        y = top_y - r * row_h
        page.ops.append(f"0 G 0.8 w {x:g} {y:g} m {x + n_cols * col_w:g} {y:g} l S".encode())
    for c in range(n_cols + 1):
        xx = x + c * col_w
        page.ops.append(f"0 G 0.8 w {xx:g} {top_y:g} m {xx:g} {bottom:g} l S".encode())
    for r, row in enumerate(rows):
        for c, cell in enumerate(row):
            page.text(cell, x + c * col_w + 4, top_y - (r + 1) * row_h + 5, 10)
    return bottom


def _pdf(tmp: pathlib.Path, pages: list[Page]) -> str:
    p = tmp / "t.pdf"
    p.write_bytes(pdf_bytes(pages))
    return str(p)


@pytest.mark.skipif(not AMD.exists(), reason="eval sources not fetched (make eval-fetch)")
def test_table_markdown_snapshot() -> None:
    doc = PdfExtractor(page_numbers=[48]).extract(str(AMD))
    doc.check_offsets()
    (table,) = [e for e in doc.elements if e.kind == "table"]
    assert table.text + "\n" == SNAPSHOT.read_text()
    assert table.table_header == table.text.split("\n")[0]
    page = doc.pages[table.page_index]
    synth = {page.text[i] for i in range(table.start, table.end) if page.char_to_glyph[i] == SYNTH}
    assert synth <= {" ", "\n", "|"}  # cell text is glyph-mapped; only separators are synthetic


def test_no_duplicate_text(tmp_path: pathlib.Path) -> None:
    page = Page()
    page.text("Revenue grew in every segment during the year.", 72, 720, 10)
    ruled_table(page, [["Segment", "Revenue"], ["Gaming", "6805"], ["Embedded", "4552"]], 72, 700)
    page.text("Embedded growth came from the acquisition.", 72, 600, 10)
    doc = PdfExtractor().extract(_pdf(tmp_path, [page]))
    tables = [e for e in doc.elements if e.kind == "table"]
    assert len(tables) == 1 and "| Gaming | 6805 |" in tables[0].text
    others = " ".join(e.text for e in doc.elements if e.kind != "table")
    for cell in ("Segment", "6805", "4552"):
        assert cell not in others  # each cell appears once, inside the table
    texts = [e.text for e in doc.elements]
    assert texts.index(tables[0].text) == 1  # placed between the two paragraphs


def test_header_carried_across_pages(tmp_path: pathlib.Path) -> None:
    p1, p2 = Page(), Page()
    p1.text("Quarterly results by region are shown below.", 72, 720, 10)
    ruled_table(p1, [["Region", "Q1", "Q2"], ["North", "10", "12"], ["South", "8", "9"]], 72, 140)
    ruled_table(p2, [["East", "7", "6"], ["West", "5", "4"]], 72, 740)
    p2.text("The regional mix was stable.", 72, 680, 10)
    doc = PdfExtractor().extract(_pdf(tmp_path, [p1, p2]))
    t1, t2 = [e for e in doc.elements if e.kind == "table"]
    assert t1.table_header == "| Region | Q1 | Q2 |"
    assert doc.pages[t2.page_index].page_no == 2 and t2.table_header == "| Region | Q1 | Q2 |"
    assert not t2.text.startswith("| Region")  # the continuation's own text is unchanged (I1)
    # at chunk level: the continuation chunk on page 2 starts with the carried header row,
    # whose span points back to page 1
    (cont,) = [c for c in chunk_document(doc, "T") if c.kind == "table" and c.page_end == 2]
    assert cont.original_text.startswith("| Region | Q1 | Q2 |\n| East |")
    assert cont.spans[0].page == 1 and cont.spans[-1].page == 2
