"""TXT and Markdown extractor (design §6.4): Markdown headings become `section_path`; paragraphs are
blank-line separated blocks; list items and pipe tables are recognised. One flow page, `page_no=None`."""

import re

from app.ingestion.extract.base import Element, ElementKind, ExtractedDocument, PageText

_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t#]*$")
_LIST = re.compile(r"^[ \t]*([-*+]|\d{1,3}[.)])[ \t]+\S")
_TABLE_ROW = re.compile(r"^[ \t]*\|.*\|[ \t]*$")
_FENCE = re.compile(r"^[ \t]*(```|~~~)")


def decode(data: bytes) -> tuple[str, int]:
    """UTF-8 (BOM tolerated); invalid bytes become U+FFFD and are counted. Line endings → \\n."""
    text = data.decode("utf-8-sig", errors="replace")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text, text.count("�")


class TextExtractor:
    name = "text@1"

    def __init__(self, markdown: bool) -> None:
        self.markdown = markdown

    def extract(self, path: str) -> ExtractedDocument:
        with open(path, "rb") as f:
            text, bad = decode(f.read())
        return ExtractedDocument(self.name, [PageText(None, text)], self._elements(text), bad)

    def _elements(self, text: str) -> list[Element]:
        out: list[Element] = []
        sections: list[tuple[int, str]] = []  # (level, title) stack
        block_start: int | None = None
        block_kind: ElementKind = "paragraph"
        in_fence = False
        pos = 0

        def path() -> list[str]:
            return [t for _, t in sections]

        def close(end: int) -> None:
            nonlocal block_start
            if block_start is not None:
                s, e = block_start, end
                while e > s and text[e - 1] in " \t\n":
                    e -= 1
                if e > s:
                    out.append(Element(block_kind, 0, s, e, text[s:e], path(), para_index=len(out)))
            block_start = None

        for line in text.split("\n"):
            start, end = pos, pos + len(line)
            pos = end + 1
            stripped = line.strip()
            if self.markdown and _FENCE.match(line):
                if block_start is None:
                    block_start, block_kind = start, "paragraph"
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            m = _HEADING.match(line) if self.markdown else None
            if m:
                close(start)
                level, title = len(m.group(1)), m.group(2).strip()
                while sections and sections[-1][0] >= level:
                    sections.pop()
                t0 = start + line.index(title)
                out.append(
                    Element(
                        "heading", 0, t0, t0 + len(title), title, path(), level=level, para_index=len(out)
                    )
                )
                sections.append((level, title))
                continue
            if not stripped:
                close(start)
                continue
            kind: ElementKind = "paragraph"
            if self.markdown and _TABLE_ROW.match(line):
                kind = "table"
            elif _LIST.match(line):
                kind = "list_item"
            if block_start is not None and (kind != block_kind or kind == "list_item"):
                close(start)
            if block_start is None:
                block_start, block_kind = start + (len(line) - len(line.lstrip())), kind
        close(len(text))
        return out
