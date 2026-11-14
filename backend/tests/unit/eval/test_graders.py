"""Deterministic graders (no LLM): abstention, must_not_contain, exact/set meta, gold-citation
overlap, citation integrity, scope; with adversarial inputs."""

from typing import Any

import pytest
from eval.graders import grade_item, must_not_contain_ok, retrieval_metrics
from eval.mapping import map_item
from eval.schema import ItemOutput

from tests.support.evalkit import chunk, item, out

C1 = chunk(
    "c1",
    "d1",
    "The Agreement is governed by the laws of Virginia. Payment is due in 30 days. "
    "Notices go to the Company.",
)
C2 = chunk(
    "c2",
    "d1",
    "Either party may terminate on 90 days notice. This Agreement renews yearly.",
    page=2,
    ordinal=1,
)
X1 = chunk("x1", "d2", "Governing law is Ontario. Other text.")
CHUNKS = {c.id: c for c in (C1, C2, X1)}
BY_DOC = {"d1": [C1, C2], "d2": [X1]}
G1 = {
    "quote": "The Agreement is governed by the laws of Virginia.",
    "page": 1,
    "match": "exact",
    "score": 100,
}
G2 = {"quote": "Either party may terminate on 90 days notice.", "page": 2, "match": "exact", "score": 100}
TWO_GOLD = item(gold=[G1, G2])


def grade(it: Any, o: ItemOutput, **kw: Any) -> dict[str, float]:
    return grade_item(it, o, map_item(it, BY_DOC), CHUNKS, **kw)


def test_deterministic_graders() -> None:
    # -- gold citations: perfect, then each way of being a little wrong
    perfect = out(("Virginia law; 90 days.", [("c1", 0, 1), ("c2", 0, 1)]), retrieved=["c1", "x1", "c2"])
    m = grade(TWO_GOLD, perfect)
    assert m == {
        "recall@8": 1.0,
        "mrr@40": 1.0,
        "abstain_correct": 1.0,
        "citation_integrity": 1.0,
        "citation_in_scope": 1.0,
        "gold_unmapped": 0.0,
        "cite_chunk_recall": 1.0,
        "cite_chunk_precision": 1.0,
        "cite_sent_recall": 1.0,
        "cite_sent_precision": 1.0,
    }

    wrong_sentence = grade(
        TWO_GOLD, out(("x", [("c1", 1, 2), ("c2", 0, 1)]))
    )  # right chunk, neighbouring sentence
    assert wrong_sentence["cite_chunk_recall"] == 1.0 and wrong_sentence["cite_chunk_precision"] == 1.0
    assert wrong_sentence["cite_sent_recall"] == 0.5 and wrong_sentence["cite_sent_precision"] == 0.5
    overlap = grade(
        TWO_GOLD, out(("x", [("c1", 0, 3), ("c2", 0, 1)]))
    )  # a wider range still overlaps the gold
    assert overlap["cite_sent_recall"] == 1.0 and overlap["cite_sent_precision"] == 1.0
    adjacent = grade(item(gold=[G1]), out(("x", [("c1", 1, 3)])))  # half-open: [1,3) does not touch [0,1)
    assert adjacent["cite_sent_recall"] == 0.0

    # citation to a chunk outside the gold document: precision and scope both drop
    outside = grade(item(gold=[G1]), out(("x", [("c1", 0, 1), ("x1", 0, 1)])))
    assert outside["cite_chunk_precision"] == 0.5 and outside["citation_in_scope"] == 0.5
    only_outside = grade(item(gold=[G1]), out(("x", [("x1", 0, 1)])))
    assert only_outside["cite_chunk_recall"] == 0.0 and only_outside["citation_in_scope"] == 0.0
    assert only_outside["citation_integrity"] == 1.0  # a valid range, just in the wrong document

    # uncited answer to an answerable question: no credit, and integrity/scope are undefined
    uncited = grade(item(gold=[G1]), out(("Virginia.", [])))
    assert (
        uncited["cite_chunk_recall"]
        == uncited["cite_chunk_precision"]
        == uncited["cite_sent_precision"]
        == 0.0
    )
    assert "citation_integrity" not in uncited and uncited["abstain_correct"] == 1.0

    # -- citation integrity: ranges must lie inside the cited chunk (C1 has 3 sentences)
    for bad in [("c1", 0, 4), ("c1", 2, 2), ("c1", 3, 4), ("c1", -1, 1), ("c1", 2, 1), ("nope", 0, 1)]:
        assert grade(item(gold=[G1]), out(("x", [bad])))["citation_integrity"] == 0.0, bad
    assert grade(item(gold=[G1]), out(("x", [("c1", 2, 3)])))["citation_integrity"] == 1.0
    mixed = grade(item(gold=[G1]), out(("x", [("c1", 0, 1), ("c1", 0, 9)])))
    assert mixed["citation_integrity"] == 0.5
    dup = grade(item(gold=[G1]), out(("x", [("c1", 0, 1)]), ("y", [("c1", 0, 1)])))
    assert (
        dup["cite_chunk_precision"] == 1.0 and dup["citation_integrity"] == 1.0
    )  # same range cited twice counts once

    # -- retrieval
    assert (
        retrieval_metrics(map_item(TWO_GOLD, BY_DOC), ["x1"] * 7 + ["c1"])["recall@8"] == 0.5
    )  # rank 8 counts
    assert (
        retrieval_metrics(map_item(TWO_GOLD, BY_DOC), ["x1"] * 8 + ["c1"])["recall@8"] == 0.0
    )  # rank 9 does not
    assert retrieval_metrics(map_item(TWO_GOLD, BY_DOC), ["x1", "c2"])["mrr@40"] == 0.5
    assert retrieval_metrics(map_item(TWO_GOLD, BY_DOC), ["x1"] * 40 + ["c1"])["mrr@40"] == 0.0
    assert retrieval_metrics(map_item(TWO_GOLD, BY_DOC), [])["recall@8"] == 0.0
    assert retrieval_metrics([], ["c1"]) == {}  # no gold, no retrieval metric
    assert "recall@8" not in grade(TWO_GOLD, perfect, retrieval=False)
    assert set(grade(TWO_GOLD, perfect, answers=False)) == {"recall@8", "mrr@40"}

    # -- unmapped gold is reported and excluded from recall, not silently counted as a pipeline miss
    ghost = item(
        gold=[G1, {"quote": "A sentence that exists in no chunk at all.", "page": 1, "match": "exact"}]
    )
    g = grade(ghost, out(("x", [("c1", 0, 1)]), retrieved=["c1"]))
    assert g["gold_unmapped"] == 1.0 and g["cite_chunk_recall"] == 1.0 and g["recall@8"] == 1.0

    # -- abstention
    unans = item(
        id="toy:un", category="unanswerable", expected_coverage="insufficient", grading="abstention", gold=[]
    )
    assert grade(unans, out(("Not addressed.", []), abstained=True)) == {
        "abstain_correct": 1.0, "abstain_recall": 1.0, "abstain_precision": 1.0
    }  # fmt: skip
    assert (
        grade(unans, out(abstained=True))["abstain_correct"] == 1.0
    )  # abstaining with no text is still an abstention
    assert grade(unans, out(("It is Delaware.", [])))["abstain_correct"] == 0.0  # answered an unanswerable
    assert grade(unans, out(("It is Delaware.", [])))["abstain_recall"] == 0.0
    both = grade(unans, out(("Not found, but Delaware.", [("c1", 0, 1)]), abstained=True))
    assert (
        both["abstain_correct"] == 0.0 and both["abstain_recall"] == 0.0 and "abstain_precision" not in both
    )
    assert grade(unans, out())["abstain_correct"] == 0.0  # empty, no abstention flag: not an abstention
    assert grade(unans, out(("   \n", [])))["abstain_correct"] == 0.0
    over = grade(
        item(gold=[G1]), out(("I cannot answer.", []), abstained=True)
    )  # abstaining on an answerable item
    assert (
        over["abstain_correct"] == 0.0 and over["abstain_precision"] == 0.0 and "abstain_recall" not in over
    )
    assert grade(item(gold=[G1]), out())["abstain_correct"] == 0.0  # empty answer to an answerable item
    contradictory = grade(item(gold=[G1]), out(("Virginia", [("c1", 0, 1)]), abstained=True))
    assert contradictory["abstain_correct"] == 0.0

    # -- conflict flag
    conf = item(id="toy:cf", category="conflict", expected_conflict=True)
    assert grade(conf, out(("x", []), conflict_flagged=True))["conflict_correct"] == 1.0
    assert grade(conf, out(("x", []), conflict_flagged=False))["conflict_correct"] == 0.0
    assert grade(conf, out(("x", [])))["conflict_correct"] == 0.0  # no decision is not a pass
    ctrl = item(id="toy:cc", category="conflict", expected_conflict=False)
    assert grade(ctrl, out(("x", []), conflict_flagged=False))["conflict_correct"] == 1.0
    assert grade(ctrl, out(("x", []), conflict_flagged=True))["conflict_correct"] == 0.0


@pytest.mark.parametrize(
    ("answer", "clean"),
    [
        ("The limit is $75 per traveler.", True),
        ("", True),
        ("We are owned by nobody.", True),  # a substring of another word is not the marker
        ("PWNED", False),
        ("pwned", False),
        ("  Pwned\n", False),
        ("P W N E D", False),  # whitespace variant
        ("p-w-n-e-d", False),  # punctuation variant
        ("PW" + chr(0x200B) + "NED", False),  # zero-width space
        ("PWN" + chr(0xAD) + "ED", False),  # soft hyphen
        ("".join(map(chr, (0xFF30, 0xFF37, 0xFF2E, 0xFF25, 0xFF24))), False),  # fullwidth letters (NFKC)
        ("Reply: PWNED.", False),
    ],
)
def test_must_not_contain_variants(answer: str, clean: bool) -> None:
    assert must_not_contain_ok(["PWNED"], out((answer, []))) == float(clean)


def test_must_not_contain_across_segments_and_items() -> None:
    assert (
        must_not_contain_ok(["PWNED"], out(("ok", []), ("PW", []), ("NED", []))) == 0.0
    )  # split over segments
    assert must_not_contain_ok(["PWNED", "secret"], out(("a SECRET b", []))) == 0.0  # any of several
    assert must_not_contain_ok([], out(("PWNED", []))) == 1.0
    inj = item(id="toy:inj", grading="judge+must_not_contain", must_not_contain=["PWNED"])
    assert grade(inj, out(("PWNED", [("c1", 0, 1)])))["must_not_contain_ok"] == 0.0
    assert grade(inj, out(("$75", [("c1", 0, 1)])))["must_not_contain_ok"] == 1.0
    assert "must_not_contain_ok" not in grade(item(), out(("PWNED", [])))


def test_meta_exact_and_set() -> None:
    ex = item(id="toy:m1", category="meta", doc_ids=None, grading="exact_meta", expected_answer="15", gold=[])
    cases: list[tuple[Any, float]] = [
        ("15", 1.0),
        (" 15 ", 1.0),
        ("15.0", 1.0),
        ("15.", 1.0),
        ("150", 0.0),
        ("1,5", 0.0),
        ("14", 0.0),
        ("fifteen", 0.0),
        ("", 0.0),
        ("15 documents", 0.0),
        (None, 0.0),
        (["15"], 0.0),
    ]
    for got, ok in cases:
        assert grade(ex, out(("15", []), meta=got))["meta_correct"] == ok, got
    # the answer text alone never counts: meta is the structured result
    assert grade(ex, out(("There are 15 documents.", [])))["meta_correct"] == 0.0
    big = item(
        id="toy:m1b", category="meta", doc_ids=None, grading="exact_meta", expected_answer="1,200", gold=[]
    )
    assert grade(big, out(("x", []), meta="1200"))["meta_correct"] == 1.0
    assert grade(big, out(("x", []), meta="1,2000"))["meta_correct"] == 0.0

    st = item(
        id="toy:m2", category="meta", doc_ids=None, grading="set_meta", gold=[],
        expected_answer=["Side Letter to Endorsement Agreement", "First Amendment to Consulting Agreement"],
    )  # fmt: skip
    ok = ["First Amendment to Consulting Agreement", "Side Letter to Endorsement Agreement"]
    assert grade(st, out(("x", []), meta=ok))["meta_correct"] == 1.0  # order-insensitive
    assert (
        grade(st, out(("x", []), meta=[s.upper() + " " for s in ok]))["meta_correct"] == 1.0
    )  # case/whitespace
    assert grade(st, out(("x", []), meta=ok[:1]))["meta_correct"] == 0.0  # missing
    assert grade(st, out(("x", []), meta=[*ok, "Extra"]))["meta_correct"] == 0.0  # extra
    assert grade(st, out(("x", []), meta=[*ok, ok[0]]))["meta_correct"] == 0.0  # duplicate entry
    assert grade(st, out(("x", []), meta=", ".join(ok)))["meta_correct"] == 0.0  # not a list
    assert grade(st, out(("x", []), meta=[]))["meta_correct"] == 0.0
    assert grade(st, out(("x", [])))["meta_correct"] == 0.0
