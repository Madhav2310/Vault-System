"""DOCX extractor: `Heading N` paragraphs become `section_path`, tables become pipe-row
markdown, body order is preserved. One flow page, `page_no=None`. The archive is size/ratio checked with
`zipfile` metadata before python-docx parses anything."""

import zipfile
from collections.abc import Iterator
from typing import Any

from docx import Document
from docx.table import Table, _Cell
from docx.text.paragraph import Paragraph

from app.ingestion.errors import PermanentStageError
from app.ingestion.extract.base import Element, ElementKind, ExtractedDocument, PageText

MAX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
MAX_RATIO = 100
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class UnsafeArchiveError(PermanentStageError):
    def __init__(self, detail: str) -> None:
        super().__init__("unsafe_archive", detail)


def check_archive(path: str) -> None:
    """Raise `UnsafeArchiveError` if declared sizes or ratios exceed the caps. Reads no member data."""
    try:
        with zipfile.ZipFile(path) as z:
            infos = z.infolist()
    except zipfile.BadZipFile as e:
        raise PermanentStageError("corrupt_file", "not a valid DOCX archive") from e
    total = 0
    for i in infos:
        total += i.file_size
        if total > MAX_UNCOMPRESSED_BYTES:
            raise UnsafeArchiveError("uncompressed size over limit")
        if i.file_size and (i.compress_size == 0 or i.file_size / i.compress_size > MAX_RATIO):
            raise UnsafeArchiveError("member compression ratio over limit")


def _heading_level(style_name: str) -> int | None:
    parts = style_name.split()
    if len(parts) == 2 and parts[0].lower() == "heading" and parts[1].isdigit():
        return int(parts[1])
    return None


_TAB, _BR, _CR = f"{_W}tab", f"{_W}br", f"{_W}cr"
_TEXT = f"{_W}t"
_BLOCK_WRAPPERS = (f"{_W}sdtContent", f"{_W}ins", f"{_W}customXml", f"{_W}smartTag")


def para_text(p: Any) -> str:
    """Visible text of a `w:p` element: every `w:t` in document order, wherever it sits (plain runs, tracked
    insertions `w:ins`, hyperlinks, smart tags, fields). Tracked deletions use `w:delText` and are skipped.
    python-docx's `Paragraph.text` reads direct runs only and misses text inside `w:ins` and `w:sdt`."""
    out: list[str] = []
    for el in p.iter():
        if el.tag == _TEXT:
            out.append(el.text or "")
        elif el.tag == _TAB:
            out.append("\t")
        elif el.tag in (_BR, _CR):
            out.append("\n")
    return "".join(out)


def iter_blocks(parent: Any) -> Iterator[Any]:
    """Paragraph and table elements of a body or cell in order, looking inside content controls (`w:sdt`)
    and block-level tracked insertions."""
    for child in parent.iterchildren():
        if child.tag in (f"{_W}p", f"{_W}tbl"):
            yield child
        elif child.tag == f"{_W}sdt":
            for content in child.iterchildren(f"{_W}sdtContent"):
                yield from iter_blocks(content)
        elif child.tag in _BLOCK_WRAPPERS:
            yield from iter_blocks(child)


def _cell_text(cell: _Cell) -> str:
    paras = [" ".join(para_text(p).split()) for p in iter_blocks(cell._tc) if p.tag == f"{_W}p"]
    return " ".join(x for x in paras if x).replace("|", "\\|")


def _table_rows(table: Table) -> list[list[str]]:
    # Walk the real <w:tc> elements: python-docx's row.cells repeats a merged cell once per grid column.
    rows = [[_cell_text(_Cell(tc, table)) for tc in tr.tc_lst] for tr in table._tbl.tr_lst]
    width = max((len(r) for r in rows), default=0)
    return [r + [""] * (width - len(r)) for r in rows]


class DocxExtractor:
    name = "docx@1"

    def extract(self, path: str) -> ExtractedDocument:
        check_archive(path)
        try:
            document = Document(path)
        except Exception as e:
            raise PermanentStageError("corrupt_file", type(e).__name__) from e
        parts: list[str] = []
        elements: list[Element] = []
        sections: list[tuple[int, str]] = []
        pos = 0

        def add(kind: ElementKind, text: str, level: int | None = None, header: str | None = None) -> None:
            nonlocal pos
            if parts:
                pos += 2  # "\n\n" separator
            if kind == "heading" and level is not None:
                while sections and sections[-1][0] >= level:
                    sections.pop()
            path_ = [t for _, t in sections]
            elements.append(Element(kind, 0, pos, pos + len(text), text, path_, level, len(elements), header))
            if kind == "heading" and level is not None:
                sections.append((level, " ".join(text.split())))
            parts.append(text)
            pos += len(text)

        for child in iter_blocks(document.element.body):
            if child.tag == f"{_W}p":
                para = Paragraph(child, document)
                text = para_text(child).strip()
                if not text:
                    continue
                style = para.style.name if para.style is not None and para.style.name else ""
                level = _heading_level(style)
                if level is not None:
                    add("heading", text, level)
                elif style.startswith("List"):
                    add("list_item", text)
                else:
                    add("paragraph", text)
            elif child.tag == f"{_W}tbl":
                rows = [r for r in _table_rows(Table(child, document)) if any(r)]
                if not rows:
                    continue
                lines = ["| " + " | ".join(r) + " |" for r in rows]
                add("table", "\n".join(lines), header=lines[0])
        return ExtractedDocument(self.name, [PageText(None, "\n\n".join(parts))], elements)
