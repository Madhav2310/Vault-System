"""Index text assembly: pure functions, no I/O."""

import hashlib
from typing import Any

from app.ingestion.chunk import ChunkDraft
from app.ingestion.index_text import (
    build_index_text,
    caption_from_prefix,
    deterministic_header,
    extra_lines,
    table_header_from_extra,
)
from app.ingestion.stages import _index_fields

PREFIX = "Document: Annual Report | Section: Financials | Table: Table 3 Revenue"


def _draft(kind: str, extras: dict[str, Any], extra_line: str = "") -> ChunkDraft:
    body = "Net revenue | 1,200"
    emb = "\n".join([PREFIX, *([extra_line] if extra_line else []), body])
    return ChunkDraft(
        ordinal=0,
        kind=kind,  # type: ignore[arg-type]
        original_text=body,
        structural_prefix=PREFIX,
        section_path=["Financials", "Revenue"],
        page_start=1,
        page_end=1,
        sentences=[],
        spans=[],
        token_count=5,
        embedding_text=emb,
        content_hash=hashlib.sha256((emb + "chunker@2").encode()).hexdigest(),
        extras=extras,
    )


def test_prose_header() -> None:
    assert deterministic_header("Annual Report", ["Financials", "Revenue"]) == (
        'From "Annual Report", section Financials > Revenue.'
    )
    assert deterministic_header("", []) is None
    assert deterministic_header("T", []) == 'From "T".'


def test_table_header() -> None:
    h = deterministic_header(
        "Annual Report", ["Financials"], table_caption="Table 3 Revenue", table_header="Item | 2023 | 2024"
    )
    assert (
        h == 'From "Annual Report", section Financials. Table: Table 3 Revenue. Columns: Item | 2023 | 2024.'
    )


def test_none_is_the_chunkers_text() -> None:
    assert build_index_text("P", None, "body") == "P\nbody"
    c = _draft("table", {"table_caption": "x", "header": "A | B"}, "Header: A | B")
    f = _index_fields(c, "Annual Report", "chunker@2", "none")
    assert f == {
        "contextual_prefix": None,
        "embedding_text": c.embedding_text,
        "content_hash": c.content_hash,
    }


def test_deterministic_keeps_extra_lines_and_changes_hash() -> None:
    c = _draft("table", {"table_caption": "Table 3 Revenue", "header": "Item | 2024"}, "Header: Item | 2024")
    f = _index_fields(c, "Annual Report", "chunker@2", "deterministic")
    lines = f["embedding_text"].split("\n")
    assert lines[0] == PREFIX and lines[-1] == c.original_text
    assert lines[2] == "Header: Item | 2024"  # chunker's own line survives
    assert "Columns: Item | 2024." in lines[1] and f["contextual_prefix"] == lines[1]
    assert f["content_hash"] != c.content_hash
    assert f == _index_fields(c, "Annual Report", "chunker@2", "deterministic")  # stable


def test_extra_lines_roundtrip() -> None:
    emb = build_index_text("P", "H", "body", "Header: a | b")
    assert extra_lines("P", "H", "body", emb) == "Header: a | b"
    assert extra_lines("P", None, "body", build_index_text("P", None, "body")) == ""
    assert extra_lines("", None, "body", "whatever\nbody") == ""
    assert table_header_from_extra("Header: a | b") == "a | b" and table_header_from_extra("") is None
    assert caption_from_prefix(PREFIX) == "Table 3 Revenue" and caption_from_prefix("Document: T") is None
