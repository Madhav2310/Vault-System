"""DOCX extractor: sections, tables as markdown, location fields, archive limits."""

import pathlib
import zipfile

import pytest
from docx import Document

from app.ingestion.extract.docx import DocxExtractor, UnsafeArchiveError


def _build(tmp_path: pathlib.Path) -> str:
    d = Document()
    d.add_heading("Annual Report", level=1)
    d.add_paragraph("Intro paragraph.")
    d.add_heading("Financials", level=2)
    d.add_paragraph("Revenue grew.")
    t = d.add_table(rows=3, cols=3)
    for r, row in enumerate([["Item", "2023", "2024"], ["Revenue", "10", "12"], ["Cost", "4", "5"]]):
        for c, v in enumerate(row):
            t.cell(r, c).text = v
    d.add_paragraph("First point", style="List Bullet")
    d.add_heading("Risks", level=2)
    d.add_paragraph("Many risks.")
    d.add_heading("Appendix", level=1)
    d.add_paragraph("Extra.")
    p = tmp_path / "a.docx"
    d.save(str(p))
    return str(p)


def test_sections(tmp_path: pathlib.Path) -> None:
    doc = DocxExtractor().extract(_build(tmp_path))
    by_text = {e.text: e for e in doc.elements}
    assert by_text["Annual Report"].kind == "heading" and by_text["Annual Report"].level == 1
    assert by_text["Intro paragraph."].section_path == ["Annual Report"]
    assert by_text["Financials"].section_path == ["Annual Report"]  # parents only, as text.py
    assert by_text["Revenue grew."].section_path == ["Annual Report", "Financials"]
    assert by_text["Many risks."].section_path == ["Annual Report", "Risks"]
    assert by_text["Extra."].section_path == ["Appendix"]
    assert by_text["First point"].kind == "list_item"
    doc.check_offsets()


def test_table_markdown(tmp_path: pathlib.Path) -> None:
    doc = DocxExtractor().extract(_build(tmp_path))
    (table,) = [e for e in doc.elements if e.kind == "table"]
    assert table.text == "| Item | 2023 | 2024 |\n| Revenue | 10 | 12 |\n| Cost | 4 | 5 |"
    assert table.table_header == "| Item | 2023 | 2024 |"
    assert table.section_path == ["Annual Report", "Financials"]
    doc.check_offsets()


def test_table_merged_cells_not_duplicated(tmp_path: pathlib.Path) -> None:
    d = Document()
    t = d.add_table(rows=2, cols=3)
    t.cell(0, 0).merge(t.cell(0, 1)).text = "Wide"
    t.cell(0, 2).text = "X"
    for c, v in enumerate(["a", "b", "c"]):
        t.cell(1, c).text = v
    p = tmp_path / "m.docx"
    d.save(str(p))
    (table,) = DocxExtractor().extract(str(p)).elements
    assert table.text.count("Wide") == 1
    assert table.text.split("\n")[1] == "| a | b | c |"


def test_location_fields(tmp_path: pathlib.Path) -> None:
    doc = DocxExtractor().extract(_build(tmp_path))
    assert doc.extractor == "docx@1"
    assert len(doc.pages) == 1 and doc.pages[0].page_no is None
    assert all(e.page_index == 0 for e in doc.elements)
    assert [e.para_index for e in doc.elements] == list(range(len(doc.elements)))
    assert [e.text for e in doc.elements][:3] == ["Annual Report", "Intro paragraph.", "Financials"]


def test_unsafe_archive_ratio(tmp_path: pathlib.Path) -> None:
    p = tmp_path / "bomb.docx"
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", b"\0" * (5 * 1024 * 1024))
    with pytest.raises(UnsafeArchiveError) as ei:
        DocxExtractor().extract(str(p))
    assert ei.value.code == "unsafe_archive"
