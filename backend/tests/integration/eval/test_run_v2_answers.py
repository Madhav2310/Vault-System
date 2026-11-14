"""A persisted v2 run (DB-backed) writes the answers file next to the sidecar;
its item ids match the persisted eval_results rows. FakeProvider judges, $0."""

import json
import pathlib
from typing import Any

from eval.build.audit import load_vault_answers
from eval.judge import load_answers
from eval.run import Harness
from eval.run_v2 import ItemOutputV2, run_suite_v2
from eval.schema import AnswerSegment, CitedRange, EvalContext, GoldItemV2
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.settings import Settings
from app.llm.gateway import LlmGateway, MemoryLedger
from app.llm.pricing import DEFAULT_PRICES, PriceTable
from app.llm.prompts import default_registry
from app.llm.providers.fake import FakeProvider
from app.llm.types import ChatRequest, ChatResponse, TextPart, Usage
from tests.support.evalkit import ScriptedPipeline, Tracker, chunk
from tests.support.llm import SleepRecorder

C1 = chunk("c1", "d1", "The Agreement is governed by the laws of Virginia. Payment is due in 30 days.")
UNITS = ["The Agreement is governed by the laws of Virginia.", "Payment is due in 30 days."]


class AllPass(FakeProvider):
    async def complete(self, req: ChatRequest) -> ChatResponse:
        self.requests.append(req)
        raw = str(req.messages[0]["content"]).removeprefix("<inputs>").removesuffix("</inputs>")
        inputs = json.loads(raw)
        body: dict[str, Any]
        if "claims" in inputs:
            body = {
                "decisions": [
                    {"claim_index": c["index"], "entailed": True, "quote": "Virginia"}
                    for c in inputs["claims"]
                ]
            }
        else:
            body = {
                "decisions": [
                    {"segment_index": s["index"], "supported": True, "quote": ""} for s in inputs["segments"]
                ]
            }
        return ChatResponse((TextPart(json.dumps(body)),), "end_turn", Usage(10, 5), None, req.model)


async def test_v2_run_writes_answers_beside_sidecar(
    sm: async_sessionmaker[AsyncSession], tracker: Tracker, tmp_path: pathlib.Path
) -> None:
    s = Settings(_env_file=None, database_url="postgresql+asyncpg://x/y", redis_url="redis://x")  # type: ignore[call-arg]
    fake = AllPass()
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
        GoldItemV2.model_validate(
            {
                "id": "q1",
                "stratum": "single_span",
                "question": "Which law governs?",
                "source_doc": "d1",
                "answer_type": "answer",
                "required_claims": ["The governing law is Virginia."],
                "gold_evidence": [{"doc": "d1", "sentence_start": 0, "sentence_end": 1}],
                "doc_ids": ["d1"],
            }
        )
    ]
    out = ItemOutputV2(
        retrieved=["c1"],
        segments=[AnswerSegment("Virginia.", (CitedRange("c1", 0, 1),))],
        evidence=["c1"],
    )
    h = Harness(
        ScriptedPipeline({"q1": out}),
        {"d1": [C1]},
        EvalContext(),
        judge_gw=gw,
        gold_units=lambda d: UNITS,
    )
    run, _ = await run_suite_v2(
        items, h, name="toy", suite="full", split="all", sm=sm, report_dir=tmp_path, git_sha="abc1234"
    )
    tracker.run_ids.append(run.run_id)
    assert (tmp_path / f"{run.run_id}.json").exists()
    answers = tmp_path / f"{run.run_id}.answers.jsonl"
    assert answers.exists()
    async with sm() as s2:
        ids = {
            r[0]
            for r in (
                await s2.execute(
                    text("SELECT DISTINCT question_id FROM eval_results WHERE eval_run_id = :i"),
                    {"i": run.run_id},
                )
            ).all()
        }
    assert ids == {r.item_id for r in load_answers(str(answers))} == set(load_vault_answers(answers))
    assert json.loads(answers.read_text())["failed"] is False
