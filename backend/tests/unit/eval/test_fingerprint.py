"""Run fingerprints and the strict paired comparison. Synthetic sidecars, no model, no database."""

import json
import pathlib
from typing import Any

import pytest
from eval import fingerprint, paired
from eval.judge import PROMPTS
from eval.rejudge import rejudged_sidecar
from eval.report import Sidecar, SidecarItem
from eval.run import Harness, run_suite
from eval.schema import AnswerSegment, EvalContext, GoldItem, ItemOutput

from tests.support.evalkit import ScriptedPipeline, chunk

IDS = ["a1", "a2", "a3", "a4"]
# synthetic versions, deliberately not the shipped ones
ANSWER = {
    "prompts": ["answer_rag@7", "analyze_query@2"],
    "models": {"fast": "m1"},
    "retrieval": "retrieval@2",
    "switches": {"chat_clarify": "false"},
    "index": {"chunker": "chunker@1", "contextual_prefix": False},
}


def _fp(ids: list[str] = IDS, **over: Any) -> dict[str, Any]:
    fp = fingerprint.build(
        dataset_id="vault_eval",
        split="dev",
        split_items=[],
        evaluated_ids=ids,
        filter_spec=None,
        judge=fingerprint.judge_versions(PROMPTS),
        answer=ANSWER,
        code={"git_sha": "abc", "dirty": False},
    )
    for path, value in over.items():
        node = fp
        *parents, leaf = path.split(".")
        for p in parents:
            node = node[p]
        node[leaf] = value
    return fp


def _sc(vals: dict[str, float], fp: dict[str, Any] | None) -> Sidecar:
    items = [
        SidecarItem(item_id=k, category="lookup", split="dev", metrics={"answer_pass": v})
        for k, v in vals.items()
    ]
    return Sidecar(
        run_id="r", name="r", suite="full", split="dev", git_sha="x", config={}, fingerprint=fp, items=items
    )


def _pair(fa: dict[str, Any] | None, fb: dict[str, Any] | None) -> tuple[Sidecar, Sidecar]:
    return _sc({i: 1.0 for i in IDS}, fa), _sc({i: 0.0 for i in IDS}, fb)


def test_identical_fingerprints_compare() -> None:
    a, b = _pair(_fp(), _fp())
    assert paired.check_fingerprints(a, b) == []


def test_judge_version_difference_is_refused_and_named() -> None:
    other = _fp(**{"judge.claims": "eval_judge_claims@2"})
    a, b = _pair(_fp(), other)
    refusals = paired.check_fingerprints(a, b)
    assert len(refusals) == 1 and "judge.claims" in refusals[0]
    assert "eval_judge_claims@5" in refusals[0] and "eval_judge_claims@2" in refusals[0]


def test_allow_a_field_and_its_parent() -> None:
    other = _fp(**{"answer.prompts": ["answer_rag@3"], "answer.retrieval": "retrieval@3"})
    a, b = _pair(_fp(), other)
    assert paired.check_fingerprints(a, b)
    assert paired.check_fingerprints(a, b, ["answer.prompts"])  # retrieval still differs
    assert paired.check_fingerprints(a, b, ["answer.prompts", "retrieval"]) == []  # alias
    assert paired.check_fingerprints(a, b, ["answer"]) == []


def test_item_sets_differ_only_with_allow_items() -> None:
    a = _sc({i: 1.0 for i in IDS}, _fp())
    b = _sc({i: 1.0 for i in IDS[:2]}, _fp(IDS[:2], filter="stratum=table"))
    refusals = paired.check_fingerprints(a, b)
    assert refusals and "items.ids" in refusals[0]
    assert paired.check_fingerprints(a, b, ["items"]) == []  # also covers the filter that made the subset


def test_gold_content_and_code_differences() -> None:
    a, b = _pair(_fp(), _fp(**{"dataset.gold_sha": "other", "code.dirty": True}))
    refusals = paired.check_fingerprints(a, b, ["code"])
    assert "dataset.gold_sha" in refusals[0] and "code.dirty" not in refusals[0]


def test_unknown_allow_is_refused() -> None:
    a, b = _pair(_fp(), _fp())
    refusals = paired.check_fingerprints(a, b, ["answr.prompts"])
    assert refusals and "answr.prompts" in refusals[0]


def test_unreported_answer_config_is_not_comparable() -> None:
    nothing = fingerprint.build(
        dataset_id="d",
        split="dev",
        split_items=[],
        evaluated_ids=IDS,
        filter_spec=None,
        judge={},
        answer=None,
        code={},
    )
    a, b = _pair(nothing, nothing)
    refusals = paired.check_fingerprints(a, b)
    assert refusals and "not server-reported" in refusals[0]
    assert paired.check_fingerprints(a, b, ["answer"]) == []


def test_sidecar_without_fingerprint_is_refused() -> None:
    a, b = _pair(None, _fp())
    refusals = paired.check_fingerprints(a, b)
    assert refusals and refusals[0].startswith("A has no fingerprint")


def test_fingerprint_must_match_its_items() -> None:
    a = _sc({i: 1.0 for i in IDS[:3]}, _fp())
    b = _sc({i: 1.0 for i in IDS}, _fp())
    assert any("do not match" in r for r in paired.check_fingerprints(a, b))


def test_cli_exit_codes(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    def write(name: str, sc: Sidecar) -> str:
        p = tmp_path / name
        p.write_text(sc.model_dump_json())
        return str(p)

    same_a, same_b = _pair(_fp(), _fp())
    diff_a, diff_b = _pair(_fp(), _fp(**{"judge.claims": "x@2"}))
    bare = _sc({i: 1.0 for i in IDS}, None)
    args = ["--metric", "answer_pass"]
    assert paired.main([write("a.json", same_a), write("b.json", same_b), *args]) == 0
    assert paired.main([write("c.json", diff_a), write("d.json", diff_b), *args]) == 2
    assert "judge.claims" in capsys.readouterr().err
    assert paired.main([write("c.json", diff_a), write("d.json", diff_b), *args, "--allow", "judge"]) == 0
    assert paired.main([write("l.json", bare), write("b.json", same_b), *args]) == 2


def test_digest_is_deterministic_and_order_free() -> None:
    assert fingerprint.digest({"a": 1, "b": [1, 2]}) == fingerprint.digest({"b": [1, 2], "a": 1})
    assert fingerprint.digest(["a1"]) != fingerprint.digest(["a2"])


def test_merge_answer_config_unions_turns_and_marks_disagreement() -> None:
    c1 = {
        "prompts": ["answer_rag@7", "support_check@1"],
        "models": {"fast": "m1"},
        "retrieval": "retrieval@2",
    }
    c2 = {"prompts": ["answer_compare@2"], "models": {"fast": "m1"}, "retrieval": "retrieval@2"}
    m = fingerprint.merge_answer_config([c1, c2], {"chunker": "chunker@1"})
    assert m["prompts"] == ["answer_compare@2", "answer_rag@7", "support_check@1"]
    assert m["models"] == {"fast": "m1"} and m["retrieval"] == "retrieval@2"
    c3 = {**c2, "models": {"fast": "m2"}, "switches": {"chat_clarify": True}}
    assert fingerprint.merge_answer_config([c1, c3])["models"] == {"fast": "m1|m2"}


def _gold(id_: str, q: str) -> GoldItem:
    return GoldItem.model_validate(
        {
            "id": id_,
            "question": q,
            "answer_type": "abstain",
            "stratum": "unanswerable",
            "source_doc": "d1",
            "gold_evidence": [],
            "audit_status": "verified",
        }
    )


def test_gold_digest_tracks_content_not_order() -> None:
    a, b = _gold("x1", "one?"), _gold("x2", "two?")
    assert fingerprint.gold_digest([a, b]) == fingerprint.gold_digest([b, a])
    assert fingerprint.gold_digest([a, b])[0] != fingerprint.gold_digest([a, _gold("x2", "changed?")])[0]


async def test_run_writes_fingerprint_into_sidecar(tmp_path: pathlib.Path) -> None:
    items = [_gold("x1", "one?"), _gold("x2", "two?")]
    out = ItemOutput(retrieved=["c1"], segments=[AnswerSegment("none.")])

    async def answer_config() -> dict[str, Any]:
        return fingerprint.merge_answer_config([{"prompts": ["answer_rag@7"], "retrieval": "retrieval@2"}])

    h = Harness(
        ScriptedPipeline({"x1": out, "x2": out}),
        {"d1": [chunk("c1", "d1", "A sentence.")]},
        EvalContext(),
        gold_units=lambda d: ["A sentence."],
        answer_config=answer_config,
    )
    run, _ = await run_suite(
        items,
        h,
        name="toy",
        suite="retrieval",
        split="all",
        report_dir=tmp_path,
        git_sha="abc",
        split_items=items,
        filter_spec="stratum=unanswerable",
    )
    sc = json.loads((tmp_path / f"{run.run_id}.json").read_text())
    fp = sc["fingerprint"]
    assert fp["items"]["ids"] == ["x1", "x2"] and fp["filter"] == "stratum=unanswerable"
    assert fp["dataset"]["gold_sha"] == fingerprint.gold_digest(items)[0] and fp["dataset"]["n_gold"] == 2
    assert fp["answer"]["retrieval"] == "retrieval@2" and fp["answer"]["prompts"] == ["answer_rag@7"]
    assert set(fp["code"]) == {"git_sha", "dirty"}
    assert Sidecar.model_validate(sc).fingerprint == fp


def test_rejudged_sidecar_carries_new_judge_versions() -> None:
    old = _sc({"a1": 1.0, "a2": 0.0}, _fp(**{"judge.claims": "eval_judge_claims@2"})).model_dump()
    old["items"][0]["metrics"] = {"answer_pass": 0.0, "recall@8": 1.0}
    res = {"items": {"a1": {"new": {"answer_pass": 1.0}}}}
    out = rejudged_sidecar(old, res, fingerprint.judge_versions(PROMPTS), {"git_sha": "new", "dirty": False})
    sc = Sidecar.model_validate(out)
    assert [i.item_id for i in sc.items] == ["a1"]
    assert sc.items[0].metrics == {"recall@8": 1.0, "answer_pass": 1.0}
    assert sc.fingerprint is not None
    assert sc.fingerprint["judge"]["claims"] == "eval_judge_claims@5"
    assert sc.fingerprint["items"]["ids"] == ["a1"] and sc.fingerprint["code"]["git_sha"] == "new"
    assert sc.fingerprint["answer"] == ANSWER
    # compares against a fresh run on the same judges once items and code are allowed
    fresh = _sc({"a1": 1.0}, _fp(["a1"]))
    assert paired.check_fingerprints(fresh, sc, ["code"]) == []
