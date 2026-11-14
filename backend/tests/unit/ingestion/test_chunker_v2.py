"""S22.01 chunker@2: structure-aware boundaries, tables whole, I1 preserved."""

import hashlib

import pytest

from app.ingestion.chunk import CHUNKER_VERSION, ChunkDraft, chunk_document, count_tokens
from app.ingestion.chunk_rules import (
    CHUNKER_V2,
    CHUNKER_V2_OVERLAP,
    MAX_TOKENS,
    TABLE_MAX_TOKENS,
)
from app.ingestion.extract.base import Element, ExtractedDocument, PageText

HEADER = "| Item | Amount |"
V2 = CHUNKER_V2


def _row(i: int) -> str:
    return f"| Line item number {i} with a long description | {i * 1000} |"


def _table_text(rows: int) -> str:
    return "\n".join([HEADER, *(_row(i) for i in range(rows))])


def _doc(parts: list[tuple[str, str, list[str]]], page_no: int | None = 1) -> ExtractedDocument:
    """parts: (kind, text, section_path); one page, paragraphs separated by a blank line."""
    text = ""
    els: list[Element] = []
    for kind, body, path in parts:
        if text:
            text += "\n\n"
        start = len(text)
        text += body
        els.append(
            Element(
                kind,  # type: ignore[arg-type]
                0,
                start,
                len(text),
                body,
                section_path=list(path),
                table_header=HEADER if kind == "table" else None,
            )
        )
    return ExtractedDocument("t", [PageText(page_no, text)], els)


def _sentences(n: int, tag: str = "A") -> str:
    return " ".join(
        f"Sentence {k} of the {tag} section states an obligation of the supplier." for k in range(n)
    )


def _table_chunks(chunks: list[ChunkDraft]) -> list[ChunkDraft]:
    return [c for c in chunks if c.kind == "table"]


def test_tables() -> None:
    # sizes straddling the cap: below it the table is exactly one chunk (never spans two)
    for rows in (3, 30, 45, 79):
        table = _table_text(rows)
        assert count_tokens(table) <= TABLE_MAX_TOKENS, rows
        doc = _doc([("paragraph", "Intro sentence.", []), ("table", table, [])])
        tabs = _table_chunks(chunk_document(doc, "T", V2))
        assert len(tabs) == 1, rows
        assert tabs[0].original_text == table
        assert len(tabs[0].sentences) == rows + 1  # every row (and the header) is one handle
        assert "Header:" not in tabs[0].embedding_text  # first piece already holds the header
    assert 600 < count_tokens(_table_text(45)) <= count_tokens(_table_text(79)) <= TABLE_MAX_TOKENS
    assert count_tokens(_table_text(80)) > TABLE_MAX_TOKENS  # just over the cap: now it may split
    assert len(_table_chunks(chunk_document(_doc([("table", _table_text(80), [])]), "T", V2))) == 2

    big = _table_text(400)
    assert count_tokens(big) > TABLE_MAX_TOKENS
    doc = _doc([("paragraph", "Intro sentence.", []), ("table", big, [])])
    pieces = _table_chunks(chunk_document(doc, "T", V2))
    assert len(pieces) > 1
    for n, c in enumerate(pieces):
        assert c.token_count <= TABLE_MAX_TOKENS
        if n == 0:
            assert c.original_text.startswith(HEADER + "\n")
        else:
            assert not c.original_text.startswith(HEADER)  # header is NOT in the evidence text
            assert f"Header: {HEADER}\n" in c.embedding_text  # ... only in the index text
        assert HEADER not in c.original_text.split("\n", 1)[-1]
    lines = [ln for c in pieces for ln in c.original_text.split("\n") if ln != HEADER]
    assert lines == [_row(i) for i in range(400)]  # every row once, in order, one sentence each
    assert all(len(c.sentences) == len(c.original_text.split("\n")) for c in pieces)


def test_invariant_i1() -> None:
    para = _sentences(30, "Payment")
    table = _table_text(400)
    doc = _doc(
        [
            ("heading", "Agreement", []),
            ("paragraph", para, ["Agreement"]),
            ("heading", "Payment", ["Agreement"]),
            ("paragraph", para, ["Agreement", "Payment"]),
            ("paragraph", "Table 2: Fees by tier.", ["Agreement", "Payment"]),
            ("table", table, ["Agreement", "Payment"]),
            ("footnote", "1 A footnote sentence.", ["Agreement", "Payment"]),
        ]
    )
    for version in (V2, CHUNKER_V2_OVERLAP):
        chunks = chunk_document(doc, "T", version)
        assert chunks
        page = doc.pages[0].text
        for c in chunks:
            assert c.spans
            for sp in c.spans:
                assert c.original_text[sp.start : sp.end] == page[sp.page_start : sp.page_end]  # exact slice
            for s, e in c.sentences:
                assert any(sp.start <= s and e <= sp.end for sp in c.spans)  # a handle lies in one slice
            gaps = "".join(
                c.original_text[a.end : b.start] for a, b in zip(c.spans, c.spans[1:], strict=False)
            )
            assert gaps.strip() == ""  # between slices only whitespace joins, nothing re-normalized
            assert c.original_text not in ("",) and c.original_text in c.embedding_text
        # I1 for the index-only additions: they never leak into original_text
        assert all("Header:" not in c.original_text and "Context:" not in c.original_text for c in chunks)


def test_tables_split_at_headings_and_captions() -> None:
    doc = _doc(
        [
            ("heading", "Results", []),
            ("paragraph", _sentences(3, "Results"), ["Results"]),
            ("paragraph", "Table 3: Revenue by segment.", ["Results"]),
            ("table", _table_text(5), ["Results"]),
            ("paragraph", "After the table we discuss it.", ["Results"]),
        ]
    )
    chunks = chunk_document(doc, "T", V2)
    assert [c.kind for c in chunks] == ["text", "text", "table", "text"]
    cap, tab = chunks[1], chunks[2]
    assert cap.original_text == "Table 3: Revenue by segment."
    assert cap.extras["linked_table_ordinal"] == tab.ordinal and tab.extras["caption_ordinal"] == cap.ordinal
    assert "Table: Table 3: Revenue by segment." in tab.structural_prefix
    assert tab.section_path == ["Results"]
    assert "Table 3" not in tab.original_text


def test_caption_after_table_links() -> None:
    doc = _doc(
        [
            ("table", _table_text(4), ["S"]),
            ("paragraph", "Table 9: Late caption.", ["S"]),
            ("paragraph", "Figure 1. A chart of something.", ["S"]),
        ]
    )
    chunks = chunk_document(doc, "T", V2)
    tab = _table_chunks(chunks)[0]
    assert tab.extras["table_caption"] == "Table 9: Late caption."
    assert [c.original_text for c in chunks if c.extras.get("is_caption")] == [
        "Table 9: Late caption.",
        "Figure 1. A chart of something.",
    ]


def test_token_bounds_and_section_path() -> None:
    doc = _doc(
        [
            ("heading", "A", []),
            ("paragraph", _sentences(120, "A"), ["A"]),
            ("heading", "B", ["A"]),
            ("paragraph", _sentences(120, "B"), ["A", "B"]),
        ]
    )
    chunks = chunk_document(doc, "T", V2)
    assert len(chunks) > 4 and all(c.token_count <= MAX_TOKENS for c in chunks)
    assert {tuple(c.section_path) for c in chunks} == {("A",), ("A", "B")}  # nested heading chain kept
    assert all("Section: A > B" in c.structural_prefix for c in chunks if c.section_path == ["A", "B"])
    for c in chunks:  # never mixes two sections
        names = {w for w in ("A", "B") if f"of the {w} section" in c.original_text}
        assert len(names) <= 1


def test_overlap_arm_is_index_only() -> None:
    doc = _doc(
        [
            ("paragraph", _sentences(120, "A"), ["A"]),
            ("heading", "B", []),
            ("paragraph", "x y z.", ["B"]),
        ]
    )
    plain = chunk_document(doc, "T", V2)
    over = chunk_document(doc, "T", CHUNKER_V2_OVERLAP)
    assert [c.original_text for c in plain] == [c.original_text for c in over]  # evidence identical
    assert [c.sentences for c in plain] == [c.sentences for c in over]
    assert "Context:" not in plain[1].embedding_text and "Context:" in over[1].embedding_text
    assert "Context:" not in over[0].embedding_text  # first chunk has no previous
    prev = over[0]
    k = -(-len(prev.sentences) // 4)  # 25% rounded up
    expect = " ".join(prev.original_text[s:e] for s, e in prev.sentences[-k:])
    assert f"Context: {expect}\n" in over[1].embedding_text
    # a section change gets no cross-section context
    assert "Context:" not in over[-1].embedding_text
    assert plain[1].content_hash != over[1].content_hash


def test_versions_recorded_and_default_unchanged(tmp_path: object) -> None:
    doc = _doc([("paragraph", _sentences(5, "A"), ["A"])])
    d = chunk_document(doc, "T")
    assert d == chunk_document(doc, "T", CHUNKER_VERSION)
    for c in d:
        assert c.content_hash == hashlib.sha256((c.embedding_text + "chunker@1").encode()).hexdigest()
    for v in (V2, CHUNKER_V2_OVERLAP):
        for c in chunk_document(doc, "T", v):
            assert c.content_hash == hashlib.sha256((c.embedding_text + v).encode()).hexdigest()
    with pytest.raises(ValueError):
        chunk_document(doc, "T", "chunker@9")


def test_deterministic() -> None:
    doc = _doc([("paragraph", _sentences(60, "A"), ["A"]), ("table", _table_text(300), ["A"])])
    for v in (V2, CHUNKER_V2_OVERLAP):
        assert [c.content_hash for c in chunk_document(doc, "T", v)] == [
            c.content_hash for c in chunk_document(doc, "T", v)
        ]


# --- edge cases ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("version", [V2, CHUNKER_V2_OVERLAP])
def test_empty_and_heading_only(version: str) -> None:
    assert chunk_document(ExtractedDocument("t", [PageText(1, "")], []), "T", version) == []
    assert chunk_document(ExtractedDocument("t", [], []), "T", version) == []
    assert chunk_document(_doc([("heading", "Only a heading", [])]), "T", version) == []


def test_one_giant_sentence() -> None:
    giant = "word " * 5000 + "end."
    doc = _doc([("paragraph", giant, [])])
    chunks = chunk_document(doc, "T", V2)
    assert len(chunks) > 1 and all(c.token_count <= MAX_TOKENS for c in chunks)
    joined = " ".join(c.original_text for c in chunks)
    assert joined.split() == giant.split()  # nothing dropped


def test_giant_sentence_cjk() -> None:
    text = "中文合同条款" * 1500
    doc = ExtractedDocument("t", [PageText(None, text)], [Element("paragraph", 0, 0, len(text), text)])
    chunks = chunk_document(doc, "T", V2)
    assert len(chunks) > 1 and all(c.token_count <= MAX_TOKENS for c in chunks)
    assert "".join(c.original_text.replace("\n\n", "") for c in chunks) == text


def test_table_at_start_and_end() -> None:
    start = chunk_document(_doc([("table", _table_text(5), []), ("paragraph", "Then text.", [])]), "T", V2)
    assert [c.kind for c in start] == ["table", "text"]
    end = chunk_document(_doc([("paragraph", "Text first.", []), ("table", _table_text(5), [])]), "T", V2)
    assert [c.kind for c in end] == ["text", "table"]
    only = chunk_document(_doc([("table", _table_text(500), [])]), "T", V2)
    assert only and all(c.kind == "table" for c in only)
    assert [c.ordinal for c in only] == list(range(len(only)))


def test_no_headings_flow_text() -> None:
    doc = _doc([("paragraph", _sentences(80), []), ("paragraph", _sentences(80, "B"), [])], page_no=None)
    chunks = chunk_document(doc, "T", V2)
    assert len(chunks) >= 2 and all(c.section_path == [] for c in chunks)
    assert all(c.page_start == 0 and c.page_end == 0 for c in chunks)
    assert all("Section: -" in c.structural_prefix for c in chunks)


def test_deeply_nested_headings() -> None:
    parts: list[tuple[str, str, list[str]]] = []
    path: list[str] = []
    for lvl in range(6):
        parts.append(("heading", f"H{lvl}", list(path)))
        path = [*path, f"H{lvl}"]
        parts.append(("paragraph", f"Body of level {lvl}. It has two sentences.", list(path)))
    chunks = chunk_document(_doc(parts), "T", V2)
    assert [c.section_path for c in chunks] == [[f"H{i}" for i in range(n + 1)] for n in range(6)]


def test_unicode_and_zero_width_preserved() -> None:
    body = "Caf\u00e9 \u200bterm\u200d sheet. Zero\u200bwidth \ufeffmarks stay. \u00a0Non-breaking too."
    doc = _doc([("paragraph", body, ["S"])])
    chunks = chunk_document(doc, "T", V2)
    assert len(chunks) == 1
    assert chunks[0].original_text == body  # exactly the stream text: no NFKC, no stripping inside
    for s, e in chunks[0].sentences:
        assert body[s:e] == chunks[0].original_text[s:e]


def test_zero_width_table_row_is_one_handle() -> None:
    table = HEADER + "\n| a​b | 1 |\n| 中文 | 2 |"
    tab = _table_chunks(chunk_document(_doc([("table", table, [])]), "T", V2))[0]
    assert tab.original_text == table and len(tab.sentences) == 3
