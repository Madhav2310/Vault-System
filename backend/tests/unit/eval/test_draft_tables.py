"""Table_lookup items drafted from parsed 10-Q tables, offline (FakeProvider dry gateway, $0)."""

import asyncio
from collections import Counter
from decimal import Decimal

import pytest
from eval.build import draft_tables as dt
from eval.judge import SpendCapReached, SpendGuard
from eval.loader import validate_items_v2

from app.llm.gateway import MemoryLedger

Unit = tuple[int, str, str]

UNITS: list[Unit] = [
    (1, "paragraph", "Intro prose that is not a table."),
    (1, "heading", "Condensed Consolidated Balance Sheets"),
    (1, "paragraph", "($ in millions)"),
    (1, "table", "|  | June 30, 2023 |  | December 31, 2022 |  |"),  # 3  header row (dates)
    (1, "table", "| Assets |  |  |  |  |"),  # 4  section row, no values
    (1, "table", "| Cash and cash equivalents | $ | 1,577 | $ | 1,200 |"),  # 5
    (1, "table", "| Total inventories (1) | 5,280 |  | 5,372 |  |"),  # 6  footnoted label
    (1, "table", "| Accumulated depreciation | (17,248) |  | (16,820) |  |"),  # 7  negative: skipped
    (1, "table", "| Earnings per share | $ | 1.20 | $ | 1.10 |"),  # 8  per share: skipped
    (1, "table", "| Products | 10 |  | 11 |  |"),  # 9  duplicate label: skipped
    (1, "table", "| Products | 20 |  | 21 |  |"),  # 10
    (1, "table", "| Notes payable 2025 | 30 |  | 31 |  |"),  # 11 digit in label: skipped
    (2, "table", "| Goodwill | 12,869 |  | 12,790 |  |"),  # 12 continues on the next page
    (2, "table", "| Description | see note |  |  |  |"),  # 13 text cell: skipped
    (2, "paragraph", "The accompanying notes are an integral part of this statement."),
    (3, "heading", "Segment table"),
    (3, "table", "| Segment | Amount |"),  # no unit: skipped
    (3, "table", "| Retail | 5 |"),
]


def _cands(units: list[Unit] | None = None) -> list[dt.Candidate]:
    return dt.table_candidates(
        units or UNITS, doc_name="ACME_2023Q2_10Q", company="Acme Corp", period="Q2 2023"
    )


def _by_label(cands: list[dt.Candidate]) -> dict[str, list[dt.Candidate]]:
    out: dict[str, list[dt.Candidate]] = {}
    for c in cands:
        out.setdefault(c.row.label, []).append(c)
    return out


def test_candidate_selection_on_fixture() -> None:
    by = _by_label(_cands())
    assert set(by) == {"Cash and cash equivalents", "Total inventories", "Goodwill"}
    cash = by["Cash and cash equivalents"]
    assert [(c.column, c.value, c.unit, c.row.idx, c.page) for c in cash] == [
        ("June 30, 2023", "1,577", "million", 5, 1),
        ("December 31, 2022", "1,200", "million", 5, 1),
    ]
    assert all(c.statement and c.doc_key == "fb_acme_2023q2_10q" for c in cash)
    assert by["Total inventories"][0].flags == ("footnoted",)  # the marker is stripped from the label
    gw = by["Goodwill"][0]
    assert gw.flags == ("multi-page",) and gw.page == 2  # header is on page 1, the row on page 2


def test_year_only_and_missing_header_tables_are_skipped() -> None:
    year_only: list[Unit] = [
        (1, "paragraph", "(in millions)"),
        (1, "table", "|  | Three months ended |  |  |"),
        (1, "table", "|  | 2023 |  | 2022 |"),
        (1, "table", "| Revenue | 10 |  | 9 |  |"),
    ]
    assert _cands(year_only) == []
    no_header: list[Unit] = [(1, "paragraph", "(in millions)"), (1, "table", "| Revenue | 10 |  | 9 |  |")]
    assert _cands(no_header) == []


def test_group_labels_prefix_the_dates() -> None:
    units: list[Unit] = [
        (1, "paragraph", "$ in millions"),
        (1, "table", "|  | Three Months Ended |  |  |  | Six Months Ended |  |  |  |"),
        (1, "table", "|  | July 29, 2023 |  | July 30, 2022 |  | July 29, 2023 |  | July 30, 2022 |  |"),
        (1, "table", "| Revenue | $ | 9,583 | $ | 10,329 | $ | 19,050 | $ | 20,976 |"),
    ]
    cols = [c.column for c in _cands(units)]
    assert cols == [
        "Three Months Ended July 29, 2023",
        "Three Months Ended July 30, 2022",
        "Six Months Ended July 29, 2023",
        "Six Months Ended July 30, 2022",
    ]


def test_claims_built_by_code_with_alternates() -> None:
    c = _by_label(_cands())["Cash and cash equivalents"][0]
    claim, alts = dt.build_claim(c)
    assert '"Cash and cash equivalents"' in claim and '"June 30, 2023"' in claim and "$1,577 million" in claim
    assert {"1,577", "1577", "$1,577 million", "1,577 million", "$1.6 billion"} <= set(alts)
    item = dt.to_item(c, "How much cash did Acme Corp hold on June 30, 2023?")
    assert item["required_claims"] == [claim] and item["alternates"] == {claim: alts}
    assert item["gold_evidence"] == [{"doc": "fb_acme_2023q2_10q", "sentence_start": 5, "sentence_end": 6}]
    assert item["stratum"] == "table_lookup" and item["audit_status"] == "draft"
    note = item["audit_note"]
    assert "ACME_2023Q2_10Q" in note and "page 1" in note and "Cash and cash equivalents" in note
    assert "June 30, 2023" in note
    fn = dt.to_item(_by_label(_cands())["Total inventories"][0], "q")["audit_note"]
    assert "footnoted" in fn
    mp = dt.to_item(_by_label(_cands())["Goodwill"][0], "q")["audit_note"]
    assert "multi-page" in mp


def test_anti_leak_and_question_checks() -> None:
    c = _by_label(_cands())["Cash and cash equivalents"][0]
    ok = "How much cash and equivalents did Acme Corp report as of June 30, 2023?"
    assert dt.check_question(c, ok) is None
    assert dt.check_question(c, "How much did Acme Corp report as of June 30, 2023 with 1,577 shown?")
    assert "figure" in (dt.check_question(c, "Did Acme Corp hold 1577 on June 30, 2023?") or "")
    assert "company" in (dt.check_question(c, "How much cash was held on June 30, 2023?") or "")
    assert "year" in (dt.check_question(c, "How much cash did Acme Corp hold?") or "")
    # a 6-word run copied from the table heading is a leak
    leak = "Does Acme Corp show condensed consolidated balance sheets ($ in millions) on June 30, 2023?"
    assert "run" in (dt.check_question(c, leak) or "")
    assert dt.shares_run("a b c d e f g", "x a b c d e f y") and not dt.shares_run("a b c d e", "a b c d e")


def test_selection_is_seeded_and_respects_the_per_document_cap() -> None:
    pool = {"ACME_2023Q2_10Q": _cands()}
    a = dt.select_candidates(pool, {}, seed=1, total=10)
    b = dt.select_candidates(pool, {}, seed=1, total=10)
    assert [dt.item_id(x) for x in a] == [dt.item_id(x) for x in b]
    assert len(a) == 3 and len({x.row.idx for x in a}) == 3  # one cell per row, 3 rows in the doc
    assert a[0].flags  # flagged rows come first
    assert len(dt.select_candidates(pool, {"fb_acme_2023q2_10q": 2}, seed=1, total=10)) == 1
    assert dt.select_candidates(pool, {"fb_acme_2023q2_10q": 3}, seed=1, total=10) == []


def test_spend_cap_enforced_before_any_call() -> None:
    ledger = MemoryLedger()
    gw = dt._dry_gateway(ledger)
    guard = SpendGuard(ledger, Decimal("0.0000001"))
    with pytest.raises(SpendCapReached):
        asyncio.run(dt.build_items(gw, guard, dt.select_candidates({"d": _cands()}, {}, seed=1, total=3)))
    assert not ledger.records  # nothing was sent


def test_dry_run_end_to_end_validates() -> None:
    ledger = MemoryLedger()
    gw = dt._dry_gateway(ledger)
    guard = SpendGuard(ledger, Decimal(1000))
    cands = dt.select_candidates({"d": _cands()}, {}, seed=1, total=3)
    res = asyncio.run(dt.build_items(gw, guard, cands, keep=3))
    assert res.candidates == 3 and len(res.items) == 3 and not res.rejected
    ds = validate_items_v2(res.items)  # schema + per-document cap
    assert Counter(i.stratum for i in ds.items) == {"table_lookup": 3}
    assert all(i.gold_evidence[0].sentence_end - i.gold_evidence[0].sentence_start == 1 for i in ds.items)
    assert all(i.question for i in ds.items)
    assert "spent $" in dt.summary(res, None, Counter(i["source_doc"] for i in res.items))
