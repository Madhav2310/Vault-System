"""S21.06: claim-level completeness, support and abstention judges; label export; calibration math."""

import json
import math
from collections.abc import Callable
from typing import Any

import pytest
import yaml
from eval import judge as J
from eval.labels.schema import LabelFile
from eval.schema import GoldItemV2

from app.core.settings import Settings
from app.llm.gateway import LlmGateway, MemoryLedger
from app.llm.pricing import DEFAULT_PRICES, PriceTable
from app.llm.prompts import default_registry
from app.llm.providers.fake import FakeProvider
from app.llm.types import ChatRequest, ChatResponse, TextPart, Usage
from tests.support.llm import SleepRecorder

Responder = Callable[[dict[str, Any]], dict[str, Any]]


class ContentProvider(FakeProvider):
    """Replies are computed from the judge input, so concurrent calls need no scripted order."""

    def __init__(self, fn: Responder) -> None:
        super().__init__()
        self.fn = fn

    async def complete(self, req: ChatRequest) -> ChatResponse:
        self.requests.append(req)
        inputs = json.loads(
            str(req.messages[0]["content"]).removeprefix("<inputs>").removesuffix("</inputs>")
        )
        body = json.dumps(self.fn(inputs))
        return ChatResponse((TextPart(body),), "end_turn", Usage(100, 20), None, req.model)


def gateway(fn: Responder) -> tuple[LlmGateway, ContentProvider]:
    fake = ContentProvider(fn)
    s = Settings(_env_file=None, database_url="postgresql+asyncpg://x/y", redis_url="redis://x")  # type: ignore[call-arg]
    gw = LlmGateway(
        chat=fake,
        embeddings=fake,
        prompts=default_registry(),
        prices=PriceTable({**DEFAULT_PRICES}),
        ledger=MemoryLedger(),
        settings=s,
        sleep=SleepRecorder(),
        rng=lambda: 0.5,
    )
    return gw, fake


def v2(**over: Any) -> GoldItemV2:
    d: dict[str, Any] = {
        "id": "q1",
        "stratum": "single_span",
        "question": "What is the fee and when is it due?",
        "source_doc": "doc_a",
        "answer_type": "answer",
        "required_claims": ["The fee is $1,000.", "The fee is due within 30 days."],
        "alternates": {"The fee is due within 30 days.": ["payable in thirty days"]},
        "gold_evidence": [{"doc": "doc_a", "sentence_start": 0, "sentence_end": 1}],
    }
    d.update(over)
    return GoldItemV2.model_validate(d)


def literal_claim_judge(inputs: dict[str, Any]) -> dict[str, Any]:
    """A scripted 'model': a claim is entailed when its key token appears in the answer."""
    ans = inputs["answer"]
    keys = {0: ("1,000", "1000"), 1: ("30", "thirty")}
    ds = []
    for c in inputs["claims"]:
        hit = next((k for k in keys[c["index"]] if k in ans), None)
        ds.append({"claim_index": c["index"], "entailed": hit is not None, "quote": hit or ""})
    return {"decisions": ds}


async def test_decisions() -> None:
    gw, fake = gateway(literal_claim_judge)
    item = v2()
    # a correct paraphrase with a different number format passes
    p = await J.judge_claims(gw, item, "The fee is USD 1000.00, payable within thirty (30) days.")
    assert p.entailed == (True, True) and p.answer_pass and p.completeness == 1.0
    # a missing required claim fails; completeness is recall over the claims
    m = await J.judge_claims(gw, item, "The fee is $1,000.")
    assert m.entailed == (True, False) and not m.answer_pass and m.completeness == 0.5
    # extra correct detail does not fail
    x = await J.judge_claims(
        gw, item, "The fee is $1,000, due within 30 days. Fees are non-refundable and invoiced quarterly."
    )
    assert x.answer_pass
    # the alternates reach the model; the judge runs on role deep through the gateway
    first = json.loads(
        str(fake.requests[0].messages[0]["content"]).removeprefix("<inputs>").removesuffix("</inputs>")
    )
    assert first["claims"][1]["accepted_variants"] == ["payable in thirty days"]
    assert {r.model for r in fake.requests} == {gw.router.role_models["deep"]}


async def test_claim_quote_not_in_answer_is_overruled() -> None:
    def liar(inputs: dict[str, Any]) -> dict[str, Any]:
        return {
            "decisions": [
                {"claim_index": c["index"], "entailed": True, "quote": "made up"} for c in inputs["claims"]
            ]
        }

    gw, _ = gateway(liar)
    j = await J.judge_claims(gw, v2(), "Nothing relevant.")
    assert j.entailed == (False, False)


async def test_incomplete_decisions_raise() -> None:
    gw, _ = gateway(lambda i: {"decisions": [{"claim_index": 0, "entailed": True, "quote": "x"}]})
    with pytest.raises(J.JudgeOutputError):
        await J.judge_claims(gw, v2(), "x")


async def test_support_judge() -> None:
    def fn(inputs: dict[str, Any]) -> dict[str, Any]:
        ds = []
        for s in inputs["segments"]:
            ok = "30" in s["text"]
            ds.append({"segment_index": s["index"], "supported": ok, "quote": "30 days" if ok else ""})
        return {"decisions": ds}

    gw, fake = gateway(fn)
    segs = [
        J.SegmentView("Notice is 30 days.", ("Either party may terminate on 30 days notice.",)),
        J.SegmentView("A $500 fee applies.", ("Either party may terminate on 30 days notice.",)),
        J.SegmentView("Uncited text.", ()),
    ]
    r = await J.judge_support(gw, segs)
    assert r.supported == (True, False, None) and r.rate == 0.5
    assert len(fake.requests) == 1  # one batched call; the uncited segment is not sent
    none = await J.judge_support(gw, [J.SegmentView("x", ())])
    assert none.supported == (None,) and math.isnan(none.rate) and len(fake.requests) == 1


async def test_support_quote_must_be_in_passages() -> None:
    gw, _ = gateway(lambda i: {"decisions": [{"segment_index": 0, "supported": True, "quote": "invented"}]})
    r = await J.judge_support(gw, [J.SegmentView("t", ("real passage",))])
    assert r.supported == (False,)


async def test_abstention_judge() -> None:
    def fn(inputs: dict[str, Any]) -> dict[str, Any]:
        declined = "do not" in inputs["answer"]
        return {"reason": "r", "behaviour": "declined" if declined else "answered_as_asked", "correct": True}

    gw, fake = gateway(fn)
    it = v2(
        id="u1",
        stratum="unanswerable",
        answer_type="abstain",
        required_claims=[],
        alternates={},
        gold_evidence=[],
    )
    assert J.abstain_applicable(it) and not J.abstain_applicable(v2())
    ok = await J.judge_abstention(gw, it, "The documents do not state a late fee.")
    bad = await J.judge_abstention(
        gw, it, "The late fee is 2%."
    )  # model said correct=True but behaviour contradicts
    assert ok.correct and not bad.correct
    sent = json.loads(
        str(fake.requests[0].messages[0]["content"]).removeprefix("<inputs>").removesuffix("</inputs>")
    )
    assert sent["case"] == "unanswerable" and "expected_behaviour" in sent
    with pytest.raises(ValueError):
        await J.judge_abstention(gw, v2(), "x")


async def test_batched_concurrently_in_order() -> None:
    gw, _ = gateway(literal_claim_judge)
    answers = [f"The fee is $1,000 due in {n} days" for n in range(20)]
    rs: list[J.ClaimJudgement] = await J.gather_bounded(
        [J.judge_claims(gw, v2(), a) for a in answers], limit=4
    )
    assert [r.entailed[1] for r in rs] == [("30" in a) for a in answers]


def test_calibration_metrics() -> None:
    # (human, judge): 6 TP, 2 FN, 8 TN, 4 FP on n = 20
    pairs = [(True, True)] * 6 + [(True, False)] * 2 + [(False, False)] * 8 + [(False, True)] * 4
    c = J.confusion(pairs)
    assert (c.tp, c.fn, c.tn, c.fp, c.n) == (6, 2, 8, 4, 20)
    assert c.tpr == pytest.approx(6 / 8)
    assert c.tnr == pytest.approx(8 / 12)
    # po = 14/20 = 0.7; pe = (8*10 + 12*10) / 400 = 0.5; kappa = 0.2 / 0.5 = 0.4
    assert c.kappa == pytest.approx(0.4)
    # perfect agreement with both classes present
    assert J.cohens_kappa(J.Confusion(5, 0, 5, 0)) == pytest.approx(1.0)
    # chance-level agreement: kappa 0
    assert J.cohens_kappa(J.Confusion(5, 5, 5, 5)) == pytest.approx(0.0)
    # undefined cases
    assert math.isnan(J.cohens_kappa(J.Confusion()))
    assert math.isnan(J.cohens_kappa(J.Confusion(tp=10)))
    assert math.isnan(J.Confusion(tp=3, fn=1).tnr)
    assert J.meets_targets(J.Confusion(45, 5, 45, 5))  # kappa 0.80, TNR 0.90
    assert not J.meets_targets(J.Confusion(40, 10, 40, 10))  # kappa 0.60
    assert not J.meets_targets(J.Confusion(50, 0, 44, 6))  # kappa 0.88 but TNR 0.88
    assert not J.meets_targets(J.Confusion())  # NaN never passes
    assert (J.Confusion(1, 1) + J.Confusion(1, 0, 2, 3)).n == 8


def _answers() -> tuple[dict[str, GoldItemV2], list[J.AnswerRecord]]:
    items: dict[str, GoldItemV2] = {}
    recs: list[J.AnswerRecord] = []
    for i in range(60):
        it = v2(id=f"q{i}", source_doc=f"doc{i % 20}")
        items[it.id] = it
        recs.append(
            J.AnswerRecord(it.id, f"answer {i}", (J.SegmentView(f"s{i}", (f"p{i}",)),), failed=i % 5 == 0)
        )
    return items, recs


def test_export_labels_oversamples_failures_and_splits_by_doc() -> None:
    items, recs = _answers()
    lf = J.export_labels(items, recs, 20, seed=7)
    assert len(lf.pairs) == 20
    assert sum(p.failed for p in lf.pairs) == 12  # 60% of 20; 12 failures exist
    again = J.export_labels(items, recs, 20, seed=7)
    assert [p.pair_id for p in again.pairs] == [p.pair_id for p in lf.pairs]  # deterministic
    docs: dict[str, set[str]] = {}
    for p in lf.pairs:
        docs.setdefault(p.source_doc, set()).add(p.split)
    assert all(len(s) == 1 for s in docs.values())  # a document never spans splits
    assert all(p.claim_labels == [None, None] and p.support_labels == [None] for p in lf.pairs)
    # round trip through YAML
    assert LabelFile.model_validate(yaml.safe_load(yaml.safe_dump(lf.model_dump(mode="json")))) == lf
    # split shares over many documents are near 15/40/45
    shares = [J.split_of_doc(f"d{i}", 1) for i in range(4000)]
    assert shares.count("few_shot") / 4000 == pytest.approx(0.15, abs=0.03)
    assert shares.count("dev") / 4000 == pytest.approx(0.40, abs=0.03)
    # fewer failures than the share: all of them, topped up with passes
    few = J.export_labels(items, [r for r in recs if not r.failed or r.item_id == "q0"], 10, seed=1)
    assert len(few.pairs) == 10 and sum(p.failed for p in few.pairs) == 1


async def test_calibrate_runs_judges_against_labels() -> None:
    gw, _ = gateway(literal_claim_judge)
    items = {"q1": v2()}
    lf = LabelFile.model_validate(
        {
            "pairs": [
                {
                    "pair_id": "a",
                    "item_id": "q1",
                    "stratum": "single_span",
                    "source_doc": "doc_a",
                    "split": "test",
                    "question": "q",
                    "answer": "The fee is $1,000 within 30 days.",
                    "required_claims": items["q1"].required_claims,
                    "claim_labels": [True, True],
                },
                {
                    "pair_id": "b",
                    "item_id": "q1",
                    "stratum": "single_span",
                    "source_doc": "doc_a",
                    "split": "test",
                    "question": "q",
                    "answer": "The fee is $1,000.",
                    "required_claims": items["q1"].required_claims,
                    "claim_labels": [True, False],
                },
                {
                    "pair_id": "c",
                    "item_id": "q1",
                    "stratum": "single_span",
                    "source_doc": "doc_a",
                    "split": "dev",
                    "question": "q",
                    "answer": "x",
                    "required_claims": items["q1"].required_claims,
                    "claim_labels": [True, True],
                },
            ]
        }
    )
    res = await J.calibrate(gw, lf, items, split="test")
    assert res["claims"] == J.Confusion(tp=3, fn=0, tn=1, fp=0)  # the dev pair is excluded
    assert res["support"].n == 0 and res["abstain"].n == 0
    assert "claims" in J.format_calibration(res, route=None)
