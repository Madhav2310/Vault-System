"""Regression tests for review findings 5 (heading whitespace) and 7 (CJK/no-space text exceeding the max)."""

import pathlib

from app.ingestion.chunk import MAX_TOKENS, chunk_document
from app.ingestion.extract.base import Element, ExtractedDocument, PageText
from app.ingestion.extract.text import TextExtractor


def test_heading_with_inner_whitespace_is_kept(tmp_path: pathlib.Path) -> None:
    p = tmp_path / "a.md"
    p.write_text("# A  B\n\nBody text here. More text.\n")
    doc = TextExtractor(markdown=True).extract(str(p))
    chunks = chunk_document(doc, "T")
    assert chunks[0].original_text.startswith("A  B")  # the heading text is part of its section's first chunk
    assert chunks[0].section_path == ["A B"]
    assert doc.elements[1].section_path == ["A B"]  # one normalized form everywhere


def test_cjk_without_spaces_respects_the_max() -> None:
    text = "中文合同条款" * 1200  # 7,200 CJK characters, no whitespace, no terminator
    doc = ExtractedDocument("t", [PageText(None, text)], [Element("paragraph", 0, 0, len(text), text)])
    chunks = chunk_document(doc, "T")
    assert len(chunks) > 1 and all(c.token_count <= MAX_TOKENS for c in chunks)
    assert "".join(c.original_text.replace("\n\n", "") for c in chunks) == text  # nothing dropped or altered
