"""`--only-dropped`: a run over the audit-dropped items of a split, marked as such."""

import json
import pathlib
from typing import Any

import pytest
from eval.run import Harness, run_suite
from eval.schema import AnswerSegment, EvalContext, GoldItem, ItemOutput
from eval.splits import select_dropped, select_split, split_of

from tests.support.evalkit import ScriptedPipeline, chunk


def item(id_: str, source_doc: str, **over: Any) -> GoldItem:
    d: dict[str, Any] = {
        "id": id_,
        "stratum": "single_span",
        "question": "Which law governs?",
        "source_doc": source_doc,
        "answer_type": "answer",
        "required_claims": ["The governing law is Virginia."],
        "gold_evidence": [{"doc": "d1", "sentence_start": 0, "sentence_end": 1}],
        "doc_ids": ["d1"],
    }
    d.update(over)
    return GoldItem.model_validate(d)


def _docs_in(split: str, n: int) -> list[str]:
    return [k for k in (f"doc{i}" for i in range(400)) if split_of(k) == split][:n]


TRAIN_A, TRAIN_B = _docs_in("train", 2)
DEV_A = _docs_in("dev", 1)[0]
ITEMS = [
    item("live-train", TRAIN_A),
    item("dropped-train", TRAIN_B, audit_status="dropped", audit_note="ambiguous"),
    item("dropped-dev", DEV_A, audit_status="dropped", audit_note="ambiguous"),
]


def test_selects_only_dropped_items_of_the_split() -> None:
    assert [i.id for i in select_dropped(ITEMS, "train")] == ["dropped-train"]
    assert [i.id for i in select_dropped(ITEMS, "all")] == ["dropped-train", "dropped-dev"]
    assert [i.id for i in select_split(ITEMS, "train")] == ["live-train"]  # default behaviour unchanged
    with pytest.raises(ValueError):
        select_dropped(ITEMS, "smoke")


async def test_dropped_run_runs_dropped_items_and_marks_report_and_fingerprint(
    tmp_path: pathlib.Path,
) -> None:
    out = ItemOutput(retrieved=["c1"], segments=[AnswerSegment("none.")])
    h = Harness(
        ScriptedPipeline({"dropped-train": out, "live-train": out}),
        {"d1": [chunk("c1", "d1", "A sentence.")]},
        EvalContext(),
        gold_units=lambda d: ["A sentence."],
    )
    dropped = select_dropped(ITEMS, "train")
    run, path = await run_suite(
        dropped, h, name="toy", suite="retrieval", split="train", report_dir=tmp_path,
        git_sha="abc", split_items=dropped, dropped_only=True,
    )  # fmt: skip
    assert [i.item_id for i in run.items] == ["dropped-train"]
    assert run.split == "train+dropped" and run.config["dropped_only"] is True
    assert "train+dropped" in path.read_text()
    fp = json.loads((tmp_path / f"{run.run_id}.json").read_text())["fingerprint"]
    assert fp["dataset"]["id"] == "vault_eval:dropped" and fp["dataset"]["split"] == "train+dropped"

    live = select_split(ITEMS, "train")
    normal, _ = await run_suite(
        live, h, name="toy", suite="retrieval", split="train", report_dir=tmp_path, git_sha="abc",
        split_items=live,
    )  # fmt: skip
    assert normal.split == "train" and "dropped_only" not in normal.config
    assert normal.fingerprint["dataset"]["id"] == "vault_eval"
    assert normal.fingerprint["dataset"] != run.fingerprint["dataset"]
