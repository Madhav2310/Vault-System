"""Toy retrieval -> generation -> judge pipeline over FakeProvider for the eval-engine tests. No network."""

import pathlib
import sys
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from eval.engine.batch import BatchRequest, BatchResponse
from eval.engine.cache import StageKey
from eval.engine.runner import BatchSpec, Item, Stage, StageCtx, StageOutput, Suite
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import tests.support.evalkit  # noqa: F401  (puts the repo root on sys.path so `import eval` works)
from app.llm.providers.fake import FakeProvider
from app.llm.types import ChatRequest

_ = (pathlib, sys)

ITEMS = [Item(f"q{i}", {"question": f"question {i}?"}) for i in range(3)]
SUITE = Suite("toy", ITEMS)


def req(model: str, system: str, content: str) -> ChatRequest:
    return ChatRequest(model, system, [{"role": "user", "content": content}], 64, "low")


@dataclass
class Rig:
    """`answer_prompt` / `judge_prompt` / `retrieval_cfg` are the knobs a test turns; `provider.requests`
    counts provider calls; `retrievals` counts retrieval executions. `cost_of` (optional) maps a generation
    call to a pre-inserted llm_calls id."""

    provider: FakeProvider = field(default_factory=FakeProvider)
    answer_prompt: str = "answer_rag@1"
    judge_prompt: str = "eval_judge_faithfulness@1"
    retrieval_cfg: Any = field(default_factory=lambda: {"k": 8})
    model: str = "gpt-6.1-terra"
    retrievals: int = 0
    make_call_id: Callable[[], Any] | None = None
    kill_after: int | None = None  # raise KeyboardInterrupt on the Nth generation call (a "kill")
    gen_calls: int = 0

    async def _retrieval(self, ctx: StageCtx) -> StageOutput:
        self.retrievals += 1
        return StageOutput({"chunks": [f"{ctx.item.id}:c1", f"{ctx.item.id}:c2"]})

    async def _call_ids(self) -> tuple[uuid.UUID, ...]:
        return (await self.make_call_id(),) if self.make_call_id else ()

    async def _generation(self, ctx: StageCtx) -> StageOutput:
        self.gen_calls += 1
        if self.kill_after is not None and self.gen_calls == self.kill_after:
            raise KeyboardInterrupt  # BaseException: not swallowed as a unit error
        r = await self.provider.complete(req(self.model, self.answer_prompt, ctx.item.id))
        return StageOutput({"answer": r.text, "prompt": self.answer_prompt}, await self._call_ids())

    async def _judge(self, ctx: StageCtx) -> StageOutput:
        r = await self.provider.complete(
            req(self.model, self.judge_prompt, ctx.upstream["generation"]["answer"])
        )
        return StageOutput({"verdict": r.text, "n_chunks": len(ctx.upstream["retrieval"]["chunks"])})

    def gen_batch(self) -> BatchSpec:
        def request(ctx: StageCtx) -> BatchRequest:
            return BatchRequest(
                ctx.unit, {"model": self.model, "instructions": self.answer_prompt, "input": ctx.item.id}
            )

        async def parse(ctx: StageCtx, r: BatchResponse) -> StageOutput:
            assert r.body is not None
            return StageOutput({"answer": r.body["output_text"], "prompt": self.answer_prompt})

        return BatchSpec(request, parse)

    def judge_batch(self) -> BatchSpec:
        def request(ctx: StageCtx) -> BatchRequest:
            return BatchRequest(
                ctx.unit,
                {
                    "model": self.model,
                    "instructions": self.judge_prompt,
                    "input": ctx.upstream["generation"]["answer"],
                },
            )

        async def parse(ctx: StageCtx, r: BatchResponse) -> StageOutput:
            assert r.body is not None
            return StageOutput(
                {"verdict": r.body["output_text"], "n_chunks": len(ctx.upstream["retrieval"]["chunks"])}
            )

        return BatchSpec(request, parse)

    def stages(self, *, batchable: bool = False) -> list[Stage]:
        return [
            Stage("retrieval", lambda: StageKey(self.retrieval_cfg), self._retrieval),
            Stage(
                "generation",
                lambda: StageKey({"temp": 0}, self.answer_prompt, self.model),
                self._generation,
                deps=("retrieval",),
                per_rep=True,
                batch=self.gen_batch() if batchable else None,
            ),
            Stage(
                "judge",
                lambda: StageKey({"rubric": 1}, self.judge_prompt, self.model),
                self._judge,
                deps=("retrieval", "generation"),
                per_rep=True,
                batch=self.judge_batch() if batchable else None,
            ),
        ]


async def insert_call(sm: async_sessionmaker[AsyncSession], cost: str, ids: list[uuid.UUID]) -> uuid.UUID:
    cid = uuid.uuid4()
    async with sm() as s:
        await s.execute(
            text(
                "INSERT INTO llm_calls (id, purpose, lane, provider, model, attempt, cost_usd, status) "
                "VALUES (:id, 'eval_generate', 'background', 'openai', 'gpt-6.1-terra', 1, :c, 'ok')"
            ),
            {"id": cid, "c": Decimal(cost)},
        )
        await s.commit()
    ids.append(cid)
    return cid


async def delete_calls(sm: async_sessionmaker[AsyncSession], ids: list[uuid.UUID]) -> None:
    async with sm() as s:
        await s.execute(text("DELETE FROM llm_calls WHERE id = ANY(:ids)"), {"ids": ids})
        await s.commit()


def stage_values(units: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    return {u: dict(r["stages"]) for u, r in sorted(units.items())}
