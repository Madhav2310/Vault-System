import pathlib

from app.ingestion.extract.text import TextExtractor

MD = """# Services Agreement

Intro paragraph that spans
two lines.

## Term

The term is twelve months.

- first item
- second item

### Renewal

Renews yearly.

# Appendix

| a | b |
|---|---|
| 1 | 2 |
"""


def _extract(tmp: pathlib.Path, text: str | bytes, md: bool, name: str = "doc") -> object:
    p = tmp / name
    p.write_bytes(text if isinstance(text, bytes) else text.encode())
    doc = TextExtractor(markdown=md).extract(str(p))
    doc.check_offsets()
    return doc


def test_markdown_sections(tmp_path: pathlib.Path) -> None:
    doc = _extract(tmp_path, MD, md=True)
    got = [(e.kind, e.text.split("\n")[0], e.section_path) for e in doc.elements]  # type: ignore[attr-defined]
    assert got == [
        ("heading", "Services Agreement", []),
        ("paragraph", "Intro paragraph that spans", ["Services Agreement"]),
        ("heading", "Term", ["Services Agreement"]),
        ("paragraph", "The term is twelve months.", ["Services Agreement", "Term"]),
        ("list_item", "- first item", ["Services Agreement", "Term"]),
        ("list_item", "- second item", ["Services Agreement", "Term"]),
        ("heading", "Renewal", ["Services Agreement", "Term"]),
        ("paragraph", "Renews yearly.", ["Services Agreement", "Term", "Renewal"]),
        ("heading", "Appendix", []),
        ("table", "| a | b |", ["Appendix"]),
    ]
    assert all(e.page_index == 0 and doc.pages[0].page_no is None for e in doc.elements)  # type: ignore[attr-defined]


def test_plain_paragraphs(tmp_path: pathlib.Path) -> None:
    doc = _extract(
        tmp_path, "First para\ncontinues.\n\n\nSecond para.\n  \nThird # not a heading\n", md=False
    )
    assert [e.text for e in doc.elements] == [  # type: ignore[attr-defined]
        "First para\ncontinues.",
        "Second para.",
        "Third # not a heading",
    ]
    assert all(e.kind == "paragraph" and e.section_path == [] for e in doc.elements)  # type: ignore[attr-defined]


def test_encoding_fallback(tmp_path: pathlib.Path) -> None:
    doc = _extract(tmp_path, b"caf\xe9 and \xff\xfe bytes\r\nnext line", md=False)
    assert doc.replacement_chars == 3  # type: ignore[attr-defined]
    assert doc.elements[0].text == "caf� and �� bytes\nnext line"  # type: ignore[attr-defined]
