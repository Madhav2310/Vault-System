"""Chunker: sections, size limits, overlap, tables, spans, prefixes, determinism."""

import hashlib
import itertools
import pathlib
import re
import statistics

import pytest

from app.ingestion.chunk import CHUNKER_VERSION, MAX_TOKENS, ChunkDraft, chunk_document, count_tokens
from app.ingestion.extract.base import Element, ExtractedDocument, PageText
from app.ingestion.extract.pdf import PdfExtractor
from app.ingestion.extract.text import TextExtractor

ROOT = pathlib.Path(__file__).resolve().parents[4]
AMD = ROOT / "eval" / "sources" / "fb_pdfs" / "AMD_2022_10K.pdf"
QASPER = ROOT / "eval" / "sources" / "qasper_pdfs" / "1910.03814.pdf"
needs_amd = pytest.mark.skipif(not AMD.exists(), reason="eval sources not fetched (make eval-fetch)")


@pytest.fixture(scope="module")
def amd_chunks() -> list[ChunkDraft]:
    """One full extraction of the 10-K per test module (it takes ~40 s)."""
    return chunk_document(PdfExtractor().extract(str(AMD)), "AMD 10-K")


def _sentence(i: int, topic: str) -> str:
    return f"Clause {i} of the {topic} section sets out the obligations of the supplier in plain terms."


def _md(tmp: pathlib.Path) -> ExtractedDocument:
    parts = ["# Agreement", ""]
    for name, n in (("Term", 40), ("Payment", 70), ("Termination", 12)):
        parts += [f"## {name}", ""]
        for p in range(n // 4):
            parts += [" ".join(_sentence(4 * p + k, name) for k in range(4)), ""]
    path = tmp / "agreement.md"
    path.write_text("\n".join(parts))
    return TextExtractor(markdown=True).extract(str(path))


def _page_slice(doc: ExtractedDocument, sp: object) -> str:
    return doc.pages[sp.page_index].text[sp.page_start : sp.page_end]  # type: ignore[attr-defined]


def test_respects_sections(tmp_path: pathlib.Path) -> None:
    doc = _md(tmp_path)
    chunks = chunk_document(doc, "Agreement")
    for c in chunks:
        names = set(re.findall(r"of the (\w+) section", c.original_text))
        assert len(names) <= 1, names  # never mixes two sections
        if names:
            assert c.section_path[-1] == names.pop()
    assert {c.section_path[-1] for c in chunks} == {"Term", "Payment", "Termination"}


@needs_amd
def test_max_size(amd_chunks: list[ChunkDraft]) -> None:
    assert all(c.token_count <= MAX_TOKENS for c in amd_chunks if c.kind == "text")
    long = "word " * 3000  # a run-on "sentence" with no terminator
    path_doc = ExtractedDocument(
        "t", [PageText(None, long)], [Element("paragraph", 0, 0, len(long) - 1, long[:-1])]
    )
    assert all(c.token_count <= MAX_TOKENS for c in chunk_document(path_doc, "x"))


@needs_amd
def test_median_size(amd_chunks: list[ChunkDraft]) -> None:
    # The excerpt: the business narrative and Item 1A (risk factors), pages 15-45. Over the whole filing
    # the median is lower (~166): the financial-statement notes are many short sections kept separate.
    excerpt = [c.token_count for c in amd_chunks if c.kind == "text" and 15 <= c.page_start <= 45]
    median = statistics.median(excerpt)
    assert 300 <= median <= 500, median


def test_overlap_whole_sentences(tmp_path: pathlib.Path) -> None:
    doc = _md(tmp_path)
    chunks = [c for c in chunk_document(doc, "A") if c.section_path[-1:] == ["Payment"]]
    assert len(chunks) >= 2
    for a, b in itertools.pairwise(chunks):
        a_sents = {
            _page_slice(doc, sp)[s - sp.start : e - sp.start]
            for s, e in a.sentences
            for sp in a.spans
            if sp.start <= s < sp.end
        }
        shared = [b.original_text[s:e] for s, e in b.sentences if b.original_text[s:e] in a_sents]
        assert shared, "consecutive chunks overlap"
        # the overlap is a prefix of b made of whole sentences of a
        assert b.original_text.startswith(shared[0]) and all(x in a_sents for x in shared)
        assert sum(count_tokens(x) for x in shared) <= 0.15 * 400 + 1


def _table_doc(rows: int) -> ExtractedDocument:
    header = "| Item | Amount |"
    lines = [header] + [f"| Line item number {i} with a long description | {i * 1000} |" for i in range(rows)]
    table = "\n".join(lines)
    intro = "The table lists every line item."
    text = intro + "\n\n" + table
    return ExtractedDocument(
        "t",
        [PageText(1, text)],
        [
            Element("paragraph", 0, 0, len(intro), intro),
            Element("table", 0, len(intro) + 2, len(text), table, table_header=header),
        ],
    )


def test_tables_standalone() -> None:
    chunks = chunk_document(_table_doc(5), "T")
    kinds = [c.kind for c in chunks]
    assert kinds == ["text", "table"]
    assert "| Item | Amount |" not in chunks[0].original_text and "The table" not in chunks[1].original_text
    assert len(chunks[1].sentences) == 6  # one sentence per row


def test_table_split_repeats_header() -> None:
    chunks = [c for c in chunk_document(_table_doc(200), "T") if c.kind == "table"]
    assert len(chunks) > 1
    for c in chunks:
        assert c.original_text.startswith("| Item | Amount |\n") and c.token_count <= MAX_TOKENS
    rows = [ln for c in chunks for ln in c.original_text.split("\n")[1:]]
    assert len(rows) == 200 and len(set(rows)) == 200  # every row once, header aside


def _all_corpus_like(tmp: pathlib.Path) -> list[ExtractedDocument]:
    docs = [_md(tmp), _table_doc(120)]
    if QASPER.exists():
        docs.append(PdfExtractor().extract(str(QASPER)))
    return docs


def test_sentence_offsets_valid(tmp_path: pathlib.Path) -> None:
    for doc in _all_corpus_like(tmp_path):
        for c in chunk_document(doc, "T"):
            prev = 0
            for s, e in c.sentences:
                assert prev <= s < e <= len(c.original_text)
                assert c.original_text[prev:s].strip() == ""  # only whitespace between sentences
                prev = e


def test_spans_map_back(tmp_path: pathlib.Path) -> None:
    for doc in _all_corpus_like(tmp_path):
        for c in chunk_document(doc, "T"):
            covered = [False] * len(c.original_text)
            for sp in c.spans:
                assert c.original_text[sp.start : sp.end] == _page_slice(doc, sp)
                for i in range(sp.start, sp.end):
                    covered[i] = True
            assert all(cov or c.original_text[i].isspace() for i, cov in enumerate(covered))  # joins only


def test_prefix_format(tmp_path: pathlib.Path) -> None:
    md = chunk_document(_md(tmp_path), "Services Agreement")
    payment = next(c for c in md if c.section_path[-1:] == ["Payment"])
    assert (
        payment.structural_prefix == "Document: Services Agreement | Section: Agreement > Payment"
    )  # flow doc
    pdf = chunk_document(_table_doc(3), "Report")
    assert pdf[0].structural_prefix == "Document: Report | Section: - | Page: 1"
    pat = re.compile(r"^Document: .+ \| Section: .+( \| Page: \d+(-\d+)?)?$")
    assert all(pat.match(c.structural_prefix) for c in md + pdf)


def test_embedding_text(tmp_path: pathlib.Path) -> None:
    for c in chunk_document(_md(tmp_path), "T"):
        assert c.embedding_text == c.structural_prefix + "\n" + c.original_text
        assert c.content_hash == hashlib.sha256((c.embedding_text + CHUNKER_VERSION).encode()).hexdigest()


def test_deterministic(tmp_path: pathlib.Path) -> None:
    for doc in _all_corpus_like(tmp_path):
        assert [c.content_hash for c in chunk_document(doc, "T")] == [
            c.content_hash for c in chunk_document(doc, "T")
        ]
