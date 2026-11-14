"""S21.10b: a v2 run writes `<run_id>.answers.jsonl` that `eval.build.audit` and `eval.judge --export-labels`
load as-is. FakeProvider judges, scripted pipeline, $0."""

import json
import pathlib
from typing import Any

import pytest
from eval.build.audit import load_vault_answers
from eval.judge import export_labels, load_answers
from eval.run import Harness
from eval.run_v2 import ItemOutputV2, run_suite_v2
from eval.schema import AnswerSegment, CitedRange, EvalContext, GoldItemV2

from app.core.settings import Settings
from app.llm.gateway import LlmGateway, MemoryLedger
from app.llm.pricing import DEFAULT_PRICES, PriceTable
from app.llm.prompts import default_registry
from app.llm.providers.fake import FakeProvider
from app.llm.types import ChatRequest, ChatResponse, TextPart, Usage
from tests.support.evalkit import ScriptedPipeline, chunk
from tests.support.llm import SleepRecorder

C1 = chunk("c1", "d1", "The Agreement is governed by the laws of Virginia. Payment is due in 30 days.")
UNITS = ["The Agreement is governed by the laws of Virginia.", "Payment is due in 30 days."]


class Judges(FakeProvider):
    """Claim judge: entailed iff the claim's last word is in the answer; support judge: always supported."""

    async def complete(self, req: ChatRequest) -> ChatResponse:
        self.requests.append(req)
        raw = str(req.messages[0]["content"]).removeprefix("<inputs>").removesuffix("</inputs>")
        inputs = json.loads(raw)
        body: dict[str, Any]
        if "claims" in inputs:
            ans = inputs["answer"].casefold()
            body = {"decisions": []}
            for c in inputs["claims"]:
                tok = c["claim"].rstrip(".").split()[-1].casefold()
                body["decisions"].append(
                    {"claim_index": c["index"], "entailed": tok in ans, "quote": tok if tok in ans else ""}
                )
        else:
            body = {
                "decisions": [
                    {"segment_index": s["index"], "supported": True, "quote": ""} for s in inputs["segments"]
                ]
            }
        return ChatResponse((TextPart(json.dumps(body)),), "end_turn", Usage(10, 5), None, req.model)


def v2(**over: Any) -> GoldItemV2:
    d: dict[str, Any] = {
        "id": "a1",
        "stratum": "single_span",
        "question": "Which law governs?",
        "source_doc": "d1",
        "answer_type": "answer",
        "required_claims": ["The governing law is Virginia."],
        "gold_evidence": [{"doc": "d1", "sentence_start": 0, "sentence_end": 1}],
        "doc_ids": ["d1"],
    }
    d.update(over)
    return GoldItemV2.model_validate(d)


async def test_run_writes_answers_file(tmp_path: pathlib.Path) -> None:
    s = Settings(_env_file=None, database_url="postgresql+asyncpg://x/y", redis_url="redis://x")  # type: ignore[call-arg]
    fake = Judges()
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
    items = [
        v2(),
        v2(
            id="a2",
            question="When is payment due?",
            required_claims=["Payment is due in 30 days."],
            gold_evidence=[{"doc": "d1", "sentence_start": 1, "sentence_end": 2}],
        ),
        v2(id="x1", audit_status="dropped", audit_note="ambiguous"),
    ]
    outs: Any = {
        "a1": ItemOutputV2(
            retrieved=["c1"], segments=[AnswerSegment("Virginia law governs.", (CitedRange("c1", 0, 1),))]
        ),
        "a2": ItemOutputV2(retrieved=["c1"], segments=[AnswerSegment("It is not stated.")]),
    }
    h = Harness(
        ScriptedPipeline(outs),
        {"d1": [C1]},
        EvalContext(),
        judge_gw=gw,
        gold_units=lambda d: UNITS,
    )
    run, _ = await run_suite_v2(
        items, h, name="toy", suite="full", split="all", report_dir=tmp_path, git_sha="abc1234"
    )
    path = tmp_path / f"{run.run_id}.answers.jsonl"
    assert path.exists()
    rows = [json.loads(ln) for ln in path.read_text().splitlines()]
    assert [r["item_id"] for r in rows] == ["a1", "a2"]  # dropped x1 never ran
    r1, r2 = rows
    assert r1["answer"] == "Virginia law governs." and r1["cited_chunk_ids"] == ["c1"]
    assert r1["segments"] == [
        {"text": "Virginia law governs.", "passages": ["The Agreement is governed by the laws of Virginia."]}
    ]
    assert r1["failed"] is False and r1["abstained"] is False
    assert r1["stratum"] == "single_span" and r1["source_doc"] == "d1" and r1["question"]
    assert r2["failed"] is True and r2["cited_chunk_ids"] == [] and r2["segments"][0]["passages"] == []

    # consumers load it unchanged
    assert load_vault_answers(path) == {"a1": "Virginia law governs.", "a2": "It is not stated."}
    recs = load_answers(str(path))
    assert [(r.item_id, r.failed, len(r.segments)) for r in recs] == [("a1", False, 1), ("a2", True, 1)]
    lf = export_labels({i.id: i for i in items}, recs, 2)
    assert {p.item_id for p in lf.pairs} == {"a1", "a2"}
    assert next(p for p in lf.pairs if p.item_id == "a1").segments[0].passages == [
        "The Agreement is governed by the laws of Virginia."
    ]


def _answers_files(d: pathlib.Path) -> list[pathlib.Path]:
    return list(d.glob("*.answers.jsonl"))


async def test_retrieval_suite_writes_no_answers(tmp_path: pathlib.Path) -> None:
    h = Harness(
        ScriptedPipeline({"a1": ItemOutputV2(retrieved=["c1"])}),
        {"d1": [C1]},
        EvalContext(),
        gold_units=lambda d: UNITS,
    )
    await run_suite_v2([v2()], h, name="toy", suite="retrieval", split="all", report_dir=tmp_path)
    assert not _answers_files(tmp_path)


def test_audit_rejects_sidecar_without_answers(tmp_path: pathlib.Path) -> None:
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"items": [{"item_id": "a1", "metrics": {}}]}))
    with pytest.raises(SystemExit):
        load_vault_answers(p)
