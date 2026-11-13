"""Gold-quote mapping on hand-made chunks: normalization variants, fuzzy fallback, word bounds, pages, tables,
sentence ranges, long quotes."""

from eval.mapping import map_item, map_quote

from tests.support.evalkit import chunk, item

A = chunk(
    "a",
    "d",
    "Intro sentence here. The Term is twelve (12) months from the “Effective Date”. It renews yearly.",
    1,
    0,
)
B = chunk("b", "d", "The Company may terminate on notice. Payment is due in 30 days.", 2, 1)
C = chunk("c", "d", "Company reports revenue. The Company shall pay.", 3, 2)
TABLE = chunk("t", "d", "Item | FY22 | FY21\nRevenue | 5,000 | 4,000", 4, 3, kind="table")
CH = [A, B, C, TABLE]


def hits(quote: str, page: int | None = None) -> list[tuple[str, int, int, str]]:
    t = map_quote(quote, page, CH)
    return [(h.chunk_id, h.sentence_start, h.sentence_end, h.match) for h in t.hits]


def test_exact_variants_map_to_sentence_range() -> None:
    want = [("a", 1, 2, "exact")]
    assert (
        hits('The Term is twelve (12) months from the "Effective Date".', 1) == want
    )  # curly vs straight quotes
    assert hits("the   TERM is twelve (12)\nmonths from the “effective date”.") == want  # case + whitespace
    assert hits("The Term is twelve (12) months") == want  # a fragment of the sentence
    assert hits("The Term is twelve (12) months from the “Effective Date”. It renews yearly.") == [
        ("a", 1, 3, "exact")
    ]
    assert hits("Intro sentence here. The Term is twelve") == [("a", 0, 2, "exact")]  # spans two sentences


def test_page_hint_and_word_boundaries() -> None:
    assert [h[0] for h in hits("The Company may terminate on notice.", 2)] == ["b"]
    # short quote on several pages: the labelled page wins; off-page chunks are used only as a fallback
    assert [h[0] for h in hits("Company", 3)] == ["c"]
    assert sorted(h[0] for h in hits("Company")) == ["b", "c"]
    assert [h[0] for h in hits("Company", 1)] == ["b", "c"]  # nothing on page 1: fall back to every exact hit
    assert hits("Term is twelve (1") == [
        ("a", 1, 2, "exact")
    ]  # may start/end mid-token when no bounded hit exists
    assert hits("ompany may") == [("b", 0, 1, "exact")]


def test_fuzzy_fallback_and_unmapped() -> None:
    t = map_quote("The Term is twelve (12) monhts from the Effective Date. It renews yearly", 1, CH)  # typo
    assert t.mapped and t.hits[0].chunk_id == "a" and t.hits[0].match == "fuzzy" and t.hits[0].score >= 88
    nothing = map_quote("Entirely unrelated legal boilerplate about arbitration in Geneva.", 1, CH)
    assert not nothing.mapped and nothing.reason
    assert not map_quote("   ", 1, CH).mapped


def test_table_separators_and_ref_markers() -> None:
    assert hits("Revenue 5,000 4,000", 4) == [("t", 0, 1, "exact")]
    assert hits("Item FY22 FY21") == [
        ("t", 0, 1, "exact")
    ]  # synthetic ' | ' separators are not document text
    assert (
        hits("The Company may terminate on notice. BIBREF3")[0][0] == "b"
    )  # QASPER reference marker stripped


def test_long_quote_uses_head_and_tail() -> None:
    sent = "Clause number {} says that the supplier shall deliver goods to the buyer within thirty days. "
    text = "".join(sent.format(i) for i in range(1, 9))
    big = chunk("big", "d", text, 1, 4)
    quote = text.strip()  # > 400 chars
    assert len(quote) > 400
    t = map_quote(quote, 1, [big])
    assert t.hits[0].sentence_start == 0 and t.hits[0].sentence_end == 8 and t.hits[0].match == "exact"


def test_map_item_reports_unmapped_reasons() -> None:
    it = item(
        gold=[
            {"quote": "Payment is due in 30 days.", "page": 2, "match": "exact"},
            {"quote": "Never in the doc anywhere at all.", "page": 2, "match": "not_found"},
        ]
    )
    ok, nf = map_item(it, {"d1": [chunk("z", "d1", "Payment is due in 30 days.", 2)]})
    assert ok.mapped and ok.chunk_ids == {"z"}
    assert not nf.mapped and nf.reason == "gold not located at build time"
    (missing,) = map_item(item(gold=[it.gold[0].model_dump()]), {})
    assert missing.reason == "document has no ingested chunks"
