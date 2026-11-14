"""V1 -> v2 migration, offline (FakeProvider dry gateway, $0)."""

import asyncio
import pathlib
from collections import Counter
from decimal import Decimal
from typing import Any

import pytest
from eval.build import migrate_v1 as m
from eval.judge import SpendGuard
from eval.loader import DATASET_V2_DIR, MAX_ITEMS_PER_DOC, load_dataset, validate_items_v2
from eval.mapping import ChunkInfo
from eval.schema import GoldenItem

from app.llm.gateway import MemoryLedger


def _run_dry(loader: m.DocLoader | None = None) -> m.Migration:
    ledger = MemoryLedger()
    gw = m._dry_gateway(ledger)
    guard = SpendGuard(ledger, Decimal(1000))
    existing = m.existing_counts(DATASET_V2_DIR)
    return asyncio.run(m.convert(load_dataset().items, existing, gw, guard, doc_loader=loader))


def test_all_accounted() -> None:
    v1 = load_dataset().items
    mig = _run_dry()
    ids = [c.plan.item.id for c in mig.converted]
    assert sorted(ids) == sorted(i.id for i in v1) and len(v1) == 122  # none lost, none duplicated
    assert len(mig.kept) + len(mig.dropped) == 122
    for c in mig.dropped:
        assert c.raw["audit_status"] == "dropped"
        assert c.plan.drop_reason and c.plan.drop_reason in c.raw["audit_note"]
    for c in mig.kept:
        assert c.raw["audit_status"] == "draft" and c.raw["audit_note"]
    # the merged dataset validates (schema + the 3-per-document cap across authored/ and parts/)
    raw = m.existing_raw(DATASET_V2_DIR) + [c.raw for c in mig.converted]
    ds = validate_items_v2(raw)
    live = Counter(i.source_doc for i in ds.items if i.audit_status != "dropped")
    assert max(live.values()) <= MAX_ITEMS_PER_DOC
    assert mig.spent < Decimal("0.5")  # the fake provider's simulated usage; no network is involved


def test_cap_prefers_distinct_strata() -> None:
    mig = _run_dry()
    per_doc: dict[str, list[str]] = {}
    for c in mig.kept:
        per_doc.setdefault(c.plan.source_doc, []).append(c.plan.stratum)
    for strata in per_doc.values():
        if len(strata) == MAX_ITEMS_PER_DOC:
            assert len(set(strata)) >= 2  # never three near-copies when other types exist


def _item(**kw: Any) -> GoldenItem:
    base: dict[str, Any] = {
        "id": "t:1",
        "source": "CUAD",
        "category": "lookup",
        "doc_ids": ["d1"],
        "expected_coverage": "supported",
        "grading": "judge",
        "status": "draft",
        "question": "What is the fee?",
        "expected_answer": "USD 1,000 within 30 days",
        "gold": [{"quote": "The fee is USD 1,000.", "page": 1, "match": "exact"}],
    }
    base.update(kw)
    return GoldenItem.model_validate(base)


@pytest.mark.parametrize(
    ("kw", "stratum"),
    [
        ({}, "single_span"),
        ({"question": "Under what conditions may the agreement be terminated?"}, "conditional"),
        ({"question": "Is the fee refundable unless the work is rejected?"}, "conditional"),
        ({"question": "List all parties to the agreement."}, "multi_span"),
        ({"category": "identifier"}, "single_span"),
        ({"category": "table", "fb_reasoning": "Numerical reasoning"}, "numeric_calc"),
        ({"category": "table"}, "table_lookup"),
        (
            {
                "category": "unanswerable",
                "expected_coverage": "insufficient",
                "grading": "abstention",
                "gold": [],
            },
            "unanswerable",
        ),
    ],
)
def test_stratum_rules(kw: dict[str, Any], stratum: str) -> None:
    assert m.stratum_of(_item(**kw))[0] == stratum


def test_cap_counts_existing_items() -> None:
    items = [
        _item(id=f"t:{n}", question=q)
        for n, q in enumerate(["Who pays?", "List all fees.", "If late?", "When?"])
    ]
    plans = m.plan_items(items, {"d1": 1})
    assert sum(p.keep for p in plans) == MAX_ITEMS_PER_DOC - 1
    assert all(p.drop_reason for p in plans if not p.keep)


def _draft(
    claims: list[str], alts: list[m.AlternateEntry] | None = None, opt: list[str] | None = None
) -> m.DraftItem:
    return m.DraftItem(id="t:1", required_claims=claims, optional_claims=opt or [], alternates=alts or [])


def test_check_draft() -> None:
    p = m.plan_items([_item()], {})[0]
    ok = m.check_draft(
        p,
        _draft(
            ["The fee is USD 1,000."], [m.AlternateEntry(claim="The fee is USD 1,000.", variants=["$1000"])]
        ),
    )
    assert ok.alternates == {"The fee is USD 1,000.": ["$1000"]}
    miss = m.check_draft(p, _draft(["The fee is paid on time."]))
    assert any("number-check" in n and "30" in n for n in miss.notes)
    many = m.check_draft(p, _draft([f"Claim {n} 30 1000." for n in range(8)]))
    assert len(many.required) == 6 and len(many.optional) == 2
    stray = m.check_draft(p, _draft(["c 1000 30"], [m.AlternateEntry(claim="other", variants=["x"])]))
    assert stray.alternates == {}
    with pytest.raises(ValueError):
        m.check_draft(p, _draft(["  "]))


def test_evidence_maps_to_document_level_sentences() -> None:
    p1 = ChunkInfo("d1:p1", "d1", 0, "Intro line. Other text here.", ((0, 11), (12, 28)), 1, 1)
    text2 = "Header. The fee is USD 1,000. Tail."
    p2 = ChunkInfo("d1:p2", "d1", 1, text2, ((0, 7), (8, 29), (30, 35)), 2, 2)
    dt = m.DocText((p1, p2), {"d1:p1": 0, "d1:p2": 2})
    it = _item(gold=[{"quote": "The fee is USD 1,000.", "page": 2, "match": "exact"}])
    ev = m.map_evidence(it, lambda _k: dt)
    assert ev.ranges == [{"doc": "d1", "sentence_start": 3, "sentence_end": 4}]
    gone = m.map_evidence(it, lambda _k: None)
    assert gone.unmapped == 1 and gone.ranges == []


def test_live_requires_cap() -> None:
    assert m.main([]) == 2


def test_dry_run_writes_nothing_then_outputs(tmp_path: pathlib.Path) -> None:
    before = (DATASET_V2_DIR / m.PARTS_FILE).exists()
    assert m.main(["--dry-run", "--no-map"]) == 0
    assert (DATASET_V2_DIR / m.PARTS_FILE).exists() == before
    mig = _run_dry()
    m.write_outputs(mig, tmp_path)
    review = (tmp_path / m.REVIEW_FILE).read_text()
    assert "Drop list" in review and (tmp_path / m.PARTS_FILE).exists()
