"""Re-judging stored answers offline with a scripted provider (no network, no dataset files)."""

import json
from decimal import Decimal
from typing import Any, cast

from eval import rejudge as R
from eval.judge import SpendGuard
from eval.schema import GoldItemV2

from app.llm.gateway import MemoryLedger
from tests.unit.eval.test_claim_judge import gateway


def item(**over: Any) -> GoldItemV2:
    d: dict[str, Any] = {
        "id": "q1",
        "stratum": "single_span",
        "question": "Fee?",
        "source_doc": "doc_a",
        "answer_type": "answer",
        "required_claims": ["The fee is $1,000."],
        "alternates": {},
        "gold_evidence": [{"doc": "doc_a", "sentence_start": 0, "sentence_end": 1}],
    }
    d.update(over)
    return GoldItemV2.model_validate(d)


def scripted(inputs: dict[str, Any]) -> dict[str, Any]:
    if "claims" in inputs:
        ok = "1,000" in inputs["answer"]
        return {"decisions": [{"claim_index": 0, "entailed": ok, "quote": "1,000" if ok else ""}]}
    if "segments" in inputs:
        return {
            "decisions": [
                {"segment_index": s["index"], "supported": True, "quote": ""} for s in inputs["segments"]
            ]
        }
    return {"reason": "r", "behaviour": "answered_as_asked", "correct": True}


async def test_rejudge_versions_old_vs_new_and_diff() -> None:
    gw, fake = gateway(scripted)
    items = {
        "q1": item(),
        "v1": item(id="v1", stratum="versioned", required_claims=["USD 19,400"]),
        "u1": item(
            id="u1", stratum="unanswerable", answer_type="abstain", required_claims=[], gold_evidence=[]
        ),
    }
    rows: list[dict[str, Any]] = [
        {
            "item_id": "q1",
            "answer": "The fee is $1,000.",
            "abstained": False,
            "segments": [{"text": "The fee is $1,000.", "passages": ["Fee: $1,000."]}],
        },
        {"item_id": "v1", "answer": "USD 19,400 now.", "abstained": False, "segments": []},
        {"item_id": "u1", "answer": "", "abstained": True, "segments": []},
        {"item_id": "not-in-dataset", "answer": "x", "segments": []},
    ]
    old = {
        "q1": {"answer_pass": 0.0, "claim_completeness": 0.0, "support_rate": 0.0},
        "v1": {"answer_pass": 1.0, "abstention_correct": 0.0},
        "u1": {"answer_pass": 1.0, "abstention_correct": 1.0},
    }
    guard = SpendGuard(cast(MemoryLedger, gw.ledger), Decimal("5"))
    res = await R.rejudge(gw, rows, items, guard, old=old)
    assert res["n_judged"] == 3 and res["n_skipped_unknown_item"] == 1 and res["stopped"] is None
    new = {i: x["new"] for i, x in res["items"].items()}
    assert new["q1"]["answer_pass"] == 1.0 and new["q1"]["support_rate"] == 1.0
    assert new["v1"]["abstention_correct"] == 1.0  # versioned, answered: no veto
    assert new["u1"] == {"abstention_correct": 1.0, "answer_pass": 1.0}  # empty answer: no model call
    changed = {d["item_id"]: d["changes"] for d in res["diff"]}
    assert set(changed) == {"q1", "v1"}  # u1 unchanged
    assert changed["v1"]["abstention_correct"] == {"old": 0.0, "new": 1.0}
    assert res["items"]["q1"]["judge"]["claims"][0]["quote_verified"] is True
    assert len(fake.requests) == 4  # q1: claims + support; v1: claims + abstain; u1: none
    json.dumps(res)  # serialisable
    assert "| v1 | abstention_correct | 0.00 | 1.00 |" in R.format_diff(
        res["diff"],
        {k: (v["mean"], v["n"]) for k, v in res["means_new"].items()},
        {k: (v["mean"], v["n"]) for k, v in res["means_old"].items()},
    )


async def test_rejudge_stops_at_the_cap_and_scores_nothing_partial() -> None:
    gw, _ = gateway(scripted)
    guard = SpendGuard(cast(MemoryLedger, gw.ledger), Decimal("0.0000001"))
    rows = [{"item_id": "q1", "answer": "The fee is $1,000.", "segments": []}]
    res = await R.rejudge(gw, rows, {"q1": item()}, guard)
    assert res["stopped"] == "budget" and res["n_judged"] == 0


def test_run_id_and_old_metrics_loading(tmp_path: Any) -> None:
    p = tmp_path / "abc.answers.jsonl"
    p.write_text(json.dumps({"item_id": "a"}) + "\n")
    assert R.run_id_of(p) == "abc" and R.load_rows(p) == [{"item_id": "a"}]
    (tmp_path / "abc.json").write_text(
        json.dumps({"items": [{"item_id": "a", "metrics": {"answer_pass": 1.0}}]})
    )
    assert R.load_old_metrics(tmp_path / "abc.json") == {"a": {"answer_pass": 1.0}}
    assert R.load_old_metrics(tmp_path / "missing.json") == {}


def test_output_defaults_to_the_answers_folder(tmp_path: Any) -> None:
    answers = tmp_path / "run1.answers.jsonl"
    assert R.output_path(answers, None) == tmp_path / "run1.rejudged.json"
    assert R.output_path(answers, str(tmp_path / "out")) == tmp_path / "out" / "run1.rejudged.json"


def test_refuses_to_overwrite_an_existing_result(tmp_path: Any, capsys: Any) -> None:
    answers = tmp_path / "run1.answers.jsonl"
    answers.write_text("", "utf-8")
    (tmp_path / "run1.rejudged.json").write_text("{}", "utf-8")
    assert R.main(["--answers", str(answers), "--max-usd", "0.1"]) == 2
    assert "refusing to overwrite" in capsys.readouterr().err
    assert (tmp_path / "run1.rejudged.json").read_text("utf-8") == "{}"
