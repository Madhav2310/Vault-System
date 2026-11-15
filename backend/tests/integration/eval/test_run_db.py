"""An answers run persists: one eval_runs row (config.dataset = vault_eval), one eval_results row per
item per metric, and a Markdown report with the per-stratum table. FakeProvider judges, $0."""

import json
import pathlib
from typing import Any

from eval.run import Harness, run_suite
from eval.schema import AnswerSegment, CitedRange, EvalContext, GoldItem, ItemOutput
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
        if "claims" in inputs:
            body: dict[str, Any] = {
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


async def test_answers_run_persists(
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
        GoldItem.model_validate(
            {
                "id": "p1",
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
    out = ItemOutput(
        retrieved=["c1"],
        segments=[AnswerSegment("Virginia.", (CitedRange("c1", 0, 1),))],
        evidence=["c1"],
    )
    h = Harness(
        ScriptedPipeline({"p1": out}),
        {"d1": [C1]},
        EvalContext(),
        judge_gw=gw,
        gold_units=lambda d: UNITS,
    )
    run, path = await run_suite(
        items, h, name="toy", suite="full", split="all", sm=sm, report_dir=tmp_path, git_sha="abc1234"
    )
    tracker.run_ids.append(run.run_id)
    async with sm() as s2:
        cfg = (
            await s2.execute(text("SELECT config FROM eval_runs WHERE id = :i"), {"i": run.run_id})
        ).scalar_one()
        rows = (
            await s2.execute(
                text("SELECT question_id, metric, value FROM eval_results WHERE eval_run_id = :i"),
                {"i": run.run_id},
            )
        ).all()
    assert cfg["dataset"] == "vault_eval" and cfg["n_items"] == 1
    got = {(r.question_id, r.metric): r.value for r in rows}
    assert got == {("p1", m): v for m, v in run.items[0].metrics.items()}
    assert got[("p1", "answer_pass")] == 1.0 and got[("p1", "claim_completeness")] == 1.0
    md = path.read_text()
    assert "## By stratum (diagnostic)" in md and "| single_span | 1 |" in md
    assert "claim, support and abstention judges" in md
