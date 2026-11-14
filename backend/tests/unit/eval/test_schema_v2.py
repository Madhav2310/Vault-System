"""S21.01: gold schema v2 (atomic claims) validation and v1 compatibility."""

import copy
from typing import Any

import pytest
from eval.loader import DatasetError, load_dataset, validate_items_v2
from eval.schema import GoldItemV2

GOOD: dict[str, Any] = {
    "id": "q1",
    "stratum": "single_span",
    "question": "Which law governs the agreement?",
    "source_doc": "cuad_a",
    "answer_type": "answer",
    "required_claims": ["The governing law is Delaware."],
    "alternates": {"The governing law is Delaware.": ["Delaware law governs"]},
    "gold_evidence": [{"doc": "cuad_a", "sentence_start": 4, "sentence_end": 5}],
}


def _item(**over: Any) -> dict[str, Any]:
    d = copy.deepcopy(GOOD)
    d.update(over)
    return d


def _problems(raw: list[Any]) -> str:
    with pytest.raises(DatasetError) as ei:
        validate_items_v2(raw)
    return str(ei.value)


def test_validation() -> None:
    ds = validate_items_v2([GOOD])
    assert ds.items[0].audit_status == "draft" and ds.items[0].required_claims == GOOD["required_claims"]

    assert "extra" in _problems([_item(requird_claims=["x"])]).lower()
    assert "1 to 6 required_claims, found 0" in _problems([_item(required_claims=[], alternates={})])
    assert "1 to 6 required_claims, found 7" in _problems(
        [_item(required_claims=[f"c{n}" for n in range(7)], alternates={})]
    )
    assert "abstain items must have no required_claims" in _problems([_item(answer_type="abstain")])
    assert "injection items require must_not_contain" in _problems([_item(stratum="injection")])
    assert "not one of the item's claims" in _problems([_item(alternates={"typo": ["a"]})])
    assert "sentence_end must be greater" in _problems(
        [_item(gold_evidence=[{"doc": "d", "sentence_start": 3, "sentence_end": 3}])]
    )
    assert "dropped items require an audit_note" in _problems([_item(audit_status="dropped")])
    assert "multi_turn items require prior_turns" in _problems([_item(stratum="multi_turn")])

    abstain = _item(
        id="a",
        stratum="unanswerable",
        answer_type="abstain",
        required_claims=[],
        alternates={},
        gold_evidence=[],
    )
    assert GoldItemV2.model_validate(abstain).answer_type == "abstain"

    four = [_item(id=f"q{n}") for n in range(4)]
    msg = _problems(four)
    assert "source_doc cuad_a has 4 items (max 3)" in msg and "q3" in msg
    validate_items_v2(four[:3])
    # a dropped item does not count against the cap; every bad item is reported in one run
    validate_items_v2([*four[:3], _item(id="q9", audit_status="dropped", audit_note="over cap")])
    both = _problems([_item(id="b1", required_claims=[], alternates={}), _item(id="b2", bogus=1)])
    assert "b1" in both and "b2" in both


def test_v1_still_loads() -> None:
    assert len(load_dataset().items) == 122
