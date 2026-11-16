"""The `analyze_query` output schema validates for every golden question (FakeProvider replay).
The live check is `tests/live/test_analyze_live.py`."""

import pathlib
import uuid
from typing import Any

import yaml

from app.chat.memory import Memory
from app.chat.nodes.analyze import ScopeDoc, analyze_query
from app.core.settings import Settings
from app.llm.gateway import LlmGateway, MemoryLedger
from app.llm.pricing import DEFAULT_PRICES, PriceTable
from app.llm.prompts import default_registry
from app.llm.providers.fake import FakeProvider, structured
from tests.support.llm import SleepRecorder

DATASET = pathlib.Path(__file__).resolve().parents[4] / "eval/datasets/vault_eval"


def golden_questions() -> list[tuple[str, list[str]]]:
    """Every user turn of every golden item: the earlier turns of multi-turn items, then the question."""
    out: list[tuple[str, list[str]]] = []
    for f in [*sorted((DATASET / "parts").glob("*.yaml")), *sorted((DATASET / "authored").glob("*.yaml"))]:
        items: list[dict[str, Any]] = yaml.safe_load(f.read_text())["items"]
        for it in items:
            scope = list(it.get("doc_ids") or [])
            out += [(t, scope) for t in [*(it.get("prior_turns") or []), it["question"]]]
    return out


async def test_schema_valid() -> None:
    qs = golden_questions()
    assert len(qs) >= 311  # every live item's question, plus the dropped items and the earlier turns
    docs = [ScopeDoc(uuid.uuid4(), f"Document {i}") for i in range(3)]
    fake = FakeProvider()
    settings = Settings(_env_file=None, database_url="postgresql+asyncpg://x/y", redis_url="redis://x")  # type: ignore[call-arg]
    gw = LlmGateway(
        chat=fake,
        embeddings=fake,
        prompts=default_registry(),
        prices=PriceTable({**DEFAULT_PRICES}),
        ledger=MemoryLedger(),
        settings=settings,
        sleep=SleepRecorder(),
        rng=lambda: 0.5,
    )
    for question, _doc_ids in qs:
        fake.push(
            structured(
                {
                    "standalone_query": question,
                    "target_refs": ["D1"],
                    "dimensions": [],
                    "intent": "qa",
                    "needs_whole_document": False,
                    "clarification": None,
                }
            )
        )
        res = await analyze_query(
            gw, question=question, memory=Memory(turns=[]), documents=docs, owner_id=uuid.uuid4()
        )
        assert not res.fallback, question
        assert res.standalone_query == " ".join(question.split())
        assert res.target_document_ids == (docs[0].id,)
