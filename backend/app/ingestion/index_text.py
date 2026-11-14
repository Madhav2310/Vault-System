"""Index text assembly for contextual retrieval. Pure functions, no I/O.

`embedding_text` feeds the embedding and the generated `tsv_en` column, so one string serves both search
legs: `structural_prefix`, the contextual header, optional extra lines the chunker added (`Header:` row of a
table continuation, `Context:` overlap), then `original_text`. The header lives in `chunks.contextual_prefix`
only. It is never part of `original_text` or `sentences` and is never cited (invariant I1).

`settings.index_context`: `none` (byte-for-byte the chunker's text), `deterministic` (header built by code
from the document title, section path and table caption / header row: $0, written by the chunk stage), `llm`
(header from the `chunk_context@1` prompt, written by the contextualize stage; see `contextualize.py`).
"""

import re
from collections.abc import Sequence

MAX_HEADER_ROW_CHARS = 300
MAX_HEADER_CHARS = 600
_TABLE_RE = re.compile(r"\| Table: (.*)$")
HEADER_LINE = "Header: "


def caption_from_prefix(structural_prefix: str) -> str | None:
    """The table caption the chunker appended to the prefix (`... | Table: <caption>`), if any."""
    m = _TABLE_RE.search(structural_prefix)
    return m.group(1).strip() if m else None


def deterministic_header(
    title: str,
    section_path: Sequence[str],
    *,
    table_caption: str | None = None,
    table_header: str | None = None,
) -> str | None:
    """One line built from document facts only; None when there is nothing to say."""
    parts: list[str] = []
    title = " ".join(title.split())
    section = " > ".join(" ".join(p.split()) for p in section_path if p and p.strip())
    if title and section:
        parts.append(f'From "{title}", section {section}.')
    elif title:
        parts.append(f'From "{title}".')
    elif section:
        parts.append(f"Section {section}.")
    if table_caption:
        parts.append(f"Table: {' '.join(table_caption.split())}.")
    if table_header:
        parts.append(f"Columns: {' '.join(table_header.split())[:MAX_HEADER_ROW_CHARS]}.")
    return " ".join(parts)[:MAX_HEADER_CHARS] or None


def extra_lines(
    structural_prefix: str, contextual_prefix: str | None, original_text: str, embedding_text: str
) -> str:
    """Lines the chunker put between the prefix and the text (`Header:`, `Context:`), recovered from a stored
    `embedding_text`; "" when the stored text does not have the expected shape."""
    head = f"{structural_prefix}\n"
    tail = f"\n{original_text}"
    if not structural_prefix or not embedding_text.startswith(head) or not embedding_text.endswith(tail):
        return ""
    if len(embedding_text) < len(head) + len(tail):
        return ""
    middle = embedding_text[len(head) : len(embedding_text) - len(tail)]
    if contextual_prefix:
        if middle == contextual_prefix:
            return ""
        if middle.startswith(contextual_prefix + "\n"):
            middle = middle[len(contextual_prefix) + 1 :]
    return middle


def table_header_from_extra(extra: str) -> str | None:
    for line in extra.split("\n"):
        if line.startswith(HEADER_LINE):
            return line[len(HEADER_LINE) :].strip() or None
    return None


def build_index_text(
    structural_prefix: str, contextual_prefix: str | None, original_text: str, extra: str = ""
) -> str:
    """With no header and no extra lines this is exactly `prefix + "\\n" + text` (the `none` arm)."""
    lines = [structural_prefix]
    if contextual_prefix:
        lines.append(contextual_prefix)
    if extra:
        lines.append(extra)
    lines.append(original_text)
    return "\n".join(lines)
