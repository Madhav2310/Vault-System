"""(every item validates) and AC3 (document-level holdout)."""

import dataclasses
from collections import Counter
from functools import cache

from eval.loader import Dataset, holdout_violations, load_dataset

from tests.support.evalkit import item


@cache
def _ds() -> Dataset:
    return load_dataset()


def test_schema_all_items() -> None:
    ds = _ds()  # raises DatasetError listing every invalid item
    assert len(ds.items) == 122
    assert len({i.id for i in ds.items}) == 122
    assert len(ds.documents) == 15
    cats = Counter(i.category for i in ds.items)
    assert cats["multi_turn"] == 6 and cats["conflict"] == 6 and cats["unanswerable"] == 15
    assert all(i.turns and len(i.turns) >= 2 for i in ds.items if i.category == "multi_turn")
    assert sum(1 for i in ds.items if i.rubric) == 5
    assert sum(1 for i in ds.items if i.expected_conflict is not None) == 6
    assert [i.id for i in ds.items if i.must_not_contain] == ["injection:per_diem"]
    assert {i.status for i in ds.items} == {"draft"}
    # splits: train 50, test 72, smoke 21 (README / design section 18); smoke overlays both
    assert (len(ds.splits["train"]), len(ds.splits["test"])) == (50, 72)
    assert len(ds.select("smoke")) == len(ds.splits["smoke"]) == 21
    assert len(ds.select("all")) == 122


def test_document_holdout() -> None:
    ds = _ds()
    assert ds.holdout_docs == {"auth_side_letter", "cuad_license", "fb_3m_10q", "qasper_kurdish"}
    assert holdout_violations(ds) == []
    by_id = ds.by_id()
    train_docs = {d for i in ds.splits["train"] for d in by_id[i].referenced_docs()}
    test_docs = {d for i in ds.splits["test"] for d in by_id[i].referenced_docs()}
    assert not train_docs & ds.holdout_docs  # holdout documents appear only in test
    assert ds.holdout_docs <= test_docs
    assert not {d for i in ds.splits["test"] for d in by_id[i].referenced_docs()} - set(ds.documents)

    # the check itself is live: put a holdout document into a train item and into no test item
    leak = item(id="toy:leak", doc_ids=["cuad_license"])
    broken = dataclasses.replace(
        ds,
        items=(*ds.items, leak),
        splits={**ds.splits, "train": (*ds.splits["train"], "toy:leak")},
        holdout_docs=ds.holdout_docs | {"toy_unused"},
    )
    msgs = holdout_violations(broken)
    assert any("train item toy:leak references holdout document cuad_license" in m for m in msgs)
    assert any("holdout document toy_unused is not used by any test item" in m for m in msgs)
