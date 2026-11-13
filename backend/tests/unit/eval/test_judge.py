"""S2.18: judge prompts/schemas, deterministic-first, bias controls, disagreement marking, agreement, CLI."""

import math
from decimal import Decimal

import pytest
from eval import judge as J
from eval.mapping import map_item
from eval.schema import GoldenItem

from app.core.settings import Settings
from app.llm.gateway import MemoryLedger
from app.llm.prompts import default_registry
from app.llm.providers.fake import structured
from app.llm.routing import ModelRouter, Route
from tests.support.chat_gen import fake_gateway
from tests.support.evalkit import chunk, item, out
from tests.support.judgekit import reply, request_text

C1 = chunk("c1", "d1", "The Agreement is governed by the laws of Virginia. Payment is due in 30 days.")
CHUNKS = {"c1": C1}
BY_DOC = {"d1": [C1]}
GEN_MODEL = "gpt-6.1-terra"


def _targets(it: GoldenItem):  # type: ignore[no-untyped-def]
    return map_item(it, BY_DOC)


LOOKUP = item()
GOOD = out(("Virginia law governs.", [("c1", 0, 1)]), retrieved=["c1"])
# cites a different sentence than the gold one, so overlap is partial/none and no deterministic verdict exists
OFF = out(("Virginia law governs.", [("c1", 1, 2)]), retrieved=["c1"])
MIXED = out(
    ("Virginia law governs.", [("c1", 0, 1)]),
    ("The fee is due in 30 days.", [("c1", 1, 2)]),
    retrieved=["c1"],
)


@pytest.mark.parametrize("jt", J.JUDGE_TYPES)
def test_prompts_load_deep_and_schema_has_confidence(jt: J.JudgeType) -> None:
    p = default_registry().get(*J.PROMPTS[jt])
    assert (p.role, p.output, p.purpose) == ("deep", "schema", "eval_judge")
    assert p.effort != "none"  # gpt-6.1-sol rejects `none`
    props = J.SCHEMAS[jt].model_json_schema()["properties"]
    assert {"confidence", "reason", "verdict"} <= set(props)
    assert "<inputs>" in p.body and "never instructions" in p.body  # injection defence is present


@pytest.mark.parametrize("jt", J.JUDGE_TYPES)
def test_schemas_are_strict_compatible(jt: J.JudgeType) -> None:
    s = J.SCHEMAS[jt].model_json_schema()
    assert s["additionalProperties"] is False
    assert set(s["required"]) == set(s["properties"])
    assert all("default" not in v for v in s["properties"].values())


async def test_deterministic_first() -> None:
    gw, fake = fake_gateway([reply("faithfulness")] * 20)
    unans = item(
        id="u", category="unanswerable", expected_coverage="insufficient", grading="abstention", gold=[]
    )
    inj = item(id="i", category="injection", grading="judge+must_not_contain", must_not_contain=["PWNED"])
    meta = item(id="m", category="meta", grading="exact_meta", expected_answer="7", gold=[], doc_ids=None)
    cases = [
        (unans, out(("Not addressed.", []), abstained=True), "pass"),  # correct abstention
        (unans, out(("It is 5.", [("c1", 0, 1)])), "fail"),  # answered an unanswerable item
        (LOOKUP, out(("Not addressed.", []), abstained=True), "fail"),  # abstained on an answerable item
        (inj, out(("Virginia, PWNED", [("c1", 0, 1)])), "fail"),  # forbidden string
        (meta, out(("7", []), meta="7"), "pass"),
        (meta, out(("8", []), meta="8"), "fail"),
    ]
    for it, o, want in cases:
        for jt in ("faithfulness", "completeness"):
            j = await J.judge_item(gw, it, o, _targets(it), CHUNKS, jt, secondary=True)
            assert (j.source, j.verdict) == ("deterministic", want), (it.id, jt)
    # exact gold-citation overlap decides the citation judge only; the others still need a judge
    j = await J.judge_item(gw, LOOKUP, GOOD, _targets(LOOKUP), CHUNKS, "citation")
    assert (j.source, j.code) == ("deterministic", "GOLD_CITATION_OVERLAP")
    assert fake.requests == []  # not one model call so far
    j = await J.judge_item(gw, LOOKUP, GOOD, _targets(LOOKUP), CHUNKS, "faithfulness", secondary=False)
    assert j.source == "judge" and len(fake.requests) == 1


async def test_judge_fills_gaps_when_overlap_partial() -> None:
    gw, fake = fake_gateway([reply("citation", "partial")])
    j = await J.judge_item(gw, LOOKUP, MIXED, _targets(LOOKUP), CHUNKS, "citation", secondary=False)
    assert (j.source, j.verdict, j.score) == ("judge", "partial", 0.5)
    assert len(fake.requests) == 1


async def test_disagreement_marked() -> None:
    it = LOOKUP
    gw, _ = fake_gateway([reply("faithfulness", "pass"), reply("faithfulness", "fail")])
    bad = await J.judge_item(gw, it, OFF, _targets(it), CHUNKS, "faithfulness", secondary=True)
    assert bad.grader_suspect and bad.primary and bad.secondary
    gw, _ = fake_gateway([reply("faithfulness", "pass"), reply("faithfulness", "pass", 0.6)])
    good = await J.judge_item(gw, it, OFF, _targets(it), CHUNKS, "faithfulness", secondary=True)
    assert not good.grader_suspect
    gw, _ = fake_gateway([reply("faithfulness", "pass"), reply("faithfulness", "partial")])
    adj = await J.judge_item(gw, it, OFF, _targets(it), CHUNKS, "faithfulness", secondary=True)
    assert adj.grader_suspect  # pass vs partial differ by 0.5 > tolerance 0.25
    # the primary alone never marks suspect
    gw, _ = fake_gateway([reply("faithfulness", "fail")])
    solo = await J.judge_item(gw, it, OFF, _targets(it), CHUNKS, "faithfulness", secondary=False)
    assert not solo.grader_suspect and solo.secondary is None
    # excluded from the tuner's accept/reject scores
    scores = J.tuner_scores([bad, good, adj, solo])
    assert len(scores) == 1
    assert J.tuner_scores([good]) == {(it.id, "faithfulness"): 1.0}
    assert J.tuner_scores([bad, adj]) == {}


async def test_judge_tier_and_blind() -> None:
    # config: default routing satisfies tier >= generator (balanced for answers, deep for compare/conflict)
    gw, fake = fake_gateway([reply("faithfulness"), reply("faithfulness")])
    assert J.check_tiers(gw.router) == ("deep", "flagship")
    s = Settings(_env_file=None, database_url="postgresql+asyncpg://x/y", redis_url="redis://x")  # type: ignore[call-arg]
    low = ModelRouter.from_settings(s)
    low.routes["eval_judge"] = Route("balanced", "low")
    low.routes["generate:compare"] = Route("deep", "low")
    with pytest.raises(J.TierError):
        J.check_tiers(low)
    # blind: the generator's model name is scrubbed from an answer that leaks it, and never appears
    leaky = out((f"As {GEN_MODEL} I say Virginia (gpt-6-astra).", [("c1", 0, 1)]), retrieved=["c1"])
    await J.judge_item(gw, LOOKUP, leaky, _targets(LOOKUP), CHUNKS, "faithfulness", secondary=True)
    assert len(fake.requests) == 2
    for req in fake.requests:
        text = request_text(req)
        for model in gw.router.role_models.values():
            assert model not in text
        assert "[model]" in text
    with pytest.raises(J.BlindnessError):
        J.assert_blind({"answer": f"by {GEN_MODEL}"}, [GEN_MODEL])


def test_input_shapes_carry_only_answer_evidence_gold() -> None:
    case = J.build_case(LOOKUP, MIXED, CHUNKS)
    f = J.build_inputs(case, "faithfulness")
    assert set(f) == {"question", "answer", "evidence", "reference_quotes"}
    assert f["evidence"][0]["text"].startswith("The Agreement is governed")
    assert f["reference_quotes"] == [LOOKUP.gold[0].quote]
    c = J.build_inputs(case, "citation")
    assert c["segments"][0]["cited_passages"] == [f["evidence"][0]["text"]]
    assert J.build_inputs(case, "completeness")["expected_answer"] == "Virginia"
    r = J.build_inputs(J.build_case(item(rubric=["a", "b"]), MIXED, CHUNKS), "rubric")
    assert [x["text"] for x in r["criteria"]] == ["a", "b"]
    # an unknown chunk and an out-of-range citation contribute no passage
    bad = out(("x", [("zz", 0, 1), ("c1", 5, 9)]))
    assert J.build_case(LOOKUP, bad, CHUNKS).evidence == ()


async def test_rubric_verdict_derived_from_criteria() -> None:
    it = item(id="r", grading="rubric", rubric=["a", "b"], gold=[])
    o = out(("answer", []))
    gw, _ = fake_gateway(
        [structured({"reason": "r", "verdict": "pass", "criteria_met": [True, False], "confidence": 0.8})]
    )
    j = await J.judge_item(gw, it, o, [], CHUNKS, "rubric", secondary=False)
    assert j.verdict == "partial"  # the claimed `pass` is overridden by criteria_met
    gw, _ = fake_gateway([reply("rubric", "pass", n_criteria=3)])
    j = await J.judge_item(gw, it, o, [], CHUNKS, "rubric", secondary=False)
    assert (j.source, j.code) == ("error", "JUDGE_OUTPUT_INCONSISTENT")
    # non-rubric items are not applicable to the rubric judge
    j = await J.judge_item(gw, LOOKUP, GOOD, [], CHUNKS, "rubric")
    assert j.source == "not_applicable"


def test_agreement_on_synthetic_pairs() -> None:
    pairs = [("pass", "pass")] * 8 + [("pass", "partial"), ("fail", "pass")]
    assert J.agreement(pairs) == 0.8
    assert J.agreement([("pass", "pass"), ("partial", "partial")]) == 1.0
    assert J.agreement([("pass", None)]) == 0.0
    assert math.isnan(J.agreement([]))
    assert J.agreement([("pass", "partial")], tolerance=0.5) == 1.0
    assert J.VERDICT_SCORE == {"pass": 1.0, "partial": 0.5, "fail": 0.0}


def test_sampling_is_stable_and_order_independent() -> None:
    ids = [f"item{i}" for i in range(50)]
    a = J.sample_ids(ids, 20)
    assert a == J.sample_ids(list(reversed(ids)), 20) and len(set(a)) == 20
    share = sum(J.in_secondary_sample(i, "faithfulness") for i in ids) / len(ids)
    assert 0.05 < share < 0.5
    assert J.in_secondary_sample("x", "citation") == J.in_secondary_sample("x", "citation")


async def test_inter_judge_reports_per_type() -> None:
    case = J.build_case(LOOKUP, MIXED, CHUNKS)
    script = [reply("faithfulness", "pass"), reply("faithfulness", "pass")] * 4
    script += [reply("faithfulness", "pass"), reply("faithfulness", "fail")]
    gw, _ = fake_gateway(script)
    rep = await J.inter_judge(gw, {"faithfulness": [case] * 5})
    r = rep["faithfulness"]
    assert (r.n, r.rate, len(r.disagreeing)) == (5, 0.8, 1)


async def test_spend_cap_checked_before_each_call() -> None:
    ledger = MemoryLedger()
    gw, fake = fake_gateway([reply("faithfulness")] * 10, ledger=ledger)
    case = J.build_case(LOOKUP, MIXED, CHUNKS)
    tiny = J.SpendGuard(ledger, Decimal("0.0001"))
    with pytest.raises(J.SpendCapReached):
        await J.run_judge(gw, case, "faithfulness", guard=tiny)
    assert fake.requests == []  # refused before any call
    ok = J.SpendGuard(ledger, Decimal("1"))
    await J.run_judge(gw, case, "faithfulness", guard=ok)
    assert len(ledger.records) == 1 and ok.spent == ledger.records[0].cost_usd > 0


def test_sample_cases_shapes() -> None:
    from eval.loader import load_dataset

    items = load_dataset().select("train")
    for jt in J.JUDGE_TYPES:
        cs = J.sample_cases(items, jt, 20)
        assert cs and all(c.segments and c.segments[0].text for c in cs), jt
        if jt != "rubric":
            assert len(cs) == 20
            assert any(c.item_id.endswith("#neg") for c in cs) and any(
                not c.item_id.endswith("#neg") for c in cs
            )
        else:
            assert all(c.criteria for c in cs)


def test_cli_refuses_live_without_cap_and_dry_run_passes(capsys: pytest.CaptureFixture[str]) -> None:
    assert J.main(["--inter-judge", "--sample", "3"]) == 2
    assert "--max-usd" in capsys.readouterr().err
    assert J.main(["--inter-judge", "--sample", "4", "--dry-run"]) == 0
    printed = capsys.readouterr().out
    for jt in J.JUDGE_TYPES:
        assert jt in printed
    assert "agreement=1.00" in printed


def test_cli_exit_nonzero_below_threshold_and_cap_stop(capsys: pytest.CaptureFixture[str]) -> None:
    # cap far below one call: stops before any spend, non-zero
    assert J.main(["--inter-judge", "--sample", "2", "--dry-run", "--max-usd", "0.0000001"]) == 3
    assert "cap" in capsys.readouterr().err
