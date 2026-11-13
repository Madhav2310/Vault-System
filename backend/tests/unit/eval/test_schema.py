"""S2.12 AC1: the golden-item validator rejects an item missing a required field."""

from typing import Any

import pytest
from pydantic import ValidationError

from tests.support.evalkit import item

REQUIRED = [
    "id",
    "source",
    "category",
    "doc_ids",
    "expected_coverage",
    "grading",
    "status",
    "question",
    "expected_answer",
]


@pytest.mark.parametrize("field", REQUIRED)
def test_required_fields(field: str) -> None:
    from eval.schema import GoldenItem

    raw = item().model_dump()
    raw["doc_ids"] = ["d1"]
    GoldenItem.model_validate(raw)  # the intact item is valid
    del raw[field]
    with pytest.raises(ValidationError):
        GoldenItem.model_validate(raw)


def test_required_fields_per_category_and_grading() -> None:
    gold = {"quote": "q", "page": None, "match": "exact"}
    bad: list[dict[str, Any]] = [
        {"gold": []},  # answerable judged item without gold
        {"gold": [{"quote": "q", "match": "exact"}]},  # gold without its page key
        {"gold": [{"quote": "", "page": 1, "match": "exact"}]},
        {"gold": [{"quote": "q", "page": 0, "match": "exact"}]},
        {"gold": [{"quote": "q", "page": 1, "match": "maybe"}]},
        {"gold": [gold], "doc_ids": ["a", "b"]},  # gold with no doc_id in a multi-document scope
        {"grading": "judge+must_not_contain"},  # names the grader but no list
        {"must_not_contain": ["X"]},  # list but grading does not apply it
        {"grading": "rubric"},  # rubric grading without a rubric
        {"category": "conflict"},  # conflict without expected_conflict
        {"expected_conflict": True},  # expected_conflict on a non-conflict item
        {"category": "multi_turn", "grading": "judge_per_turn"},  # no turns
        {"turns": [{"question": "q", "expected_answer": "a", "gold": []}] * 2},  # turns on a lookup
        {"category": "unanswerable"},  # unanswerable but coverage/grading disagree
        {"must_not_contian": ["X"]},  # misspelled key must not be silently ignored
        {"question": None},
    ]
    for kw in bad:
        with pytest.raises(ValidationError):
            item(**kw)
    item(category="conflict", expected_conflict=False)
    item(grading="judge+must_not_contain", must_not_contain=["PWNED"])
    item(
        category="multi_turn",
        grading="judge_per_turn",
        doc_ids=None,
        question=None,
        gold=[],
        expected_answer=None,
        turns=[
            {
                "question": "q1",
                "expected_answer": "a",
                "gold": [{"quote": "q", "page": 1, "match": "exact", "doc_id": "d1"}],
            },
            {"question": "q2", "expected_answer": "a", "gold": []},
        ],
    )
