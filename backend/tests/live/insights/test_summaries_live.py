"""Live custom-summary samples (three evidence samples).

Spends real money (asserted: all calls together < $0.10). Run with
`uv run pytest tests/live/insights/test_summaries_live.py -s -m live`; excluded from `make ci`. Needs the
corpus
sources in `eval/sources/` (a worktree falls back to the main checkout's copy) and OPENAI_API_KEY; fails, does
not skip, when they are missing. `SUMMARY_SAMPLES_OUT=<path>` also writes the summaries as JSON.
"""

import asyncio
import json
import os
import pathlib
import uuid
from decimal import Decimal
from typing import Any

import pytest
import yaml

from app.chat.evidence import EvidenceChunk
from app.core.settings import get_settings
from app.ingestion.chunk import chunk_document
from app.ingestion.extract.pdf import PdfExtractor
from app.ingestion.extract.text import TextExtractor
from app.insights.summaries import (
    WORD_LIMIT,
    ChunkRef,
    Generated,
    generate_summary,
    plan_evidence,
    word_count,
)
from app.llm.gateway import LlmGateway, MemoryLedger
from app.llm.pricing import price_table_from_settings
from app.llm.prompts import default_registry
from app.llm.providers.openai_chat import OpenAIResponsesProvider

pytestmark = pytest.mark.live

ROOT = pathlib.Path(__file__).resolve().parents[4]
DOC = "cuad_consulting"
CAP = Decimal("0.10")


def _source_roots() -> list[pathlib.Path]:
    roots = [ROOT]
    parts = ROOT.parts
    if ".claude" in parts:  # worktree: .../<repo>/.claude/worktrees/<name>
        roots.append(pathlib.Path(*parts[: parts.index(".claude")]))
    return roots


def _chunks(key: str) -> list[EvidenceChunk]:
    """The document's chunks as ingestion stores them (production extractor and chunker)."""
    meta = yaml.safe_load((ROOT / "eval/datasets/vault_eval_v1/documents.yaml").read_text())["documents"][key]
    rel = meta["file"]
    path = next((r / rel for r in _source_roots() if (r / rel).is_file()), None)
    if path is None:
        pytest.fail(f"{rel} not found under {_source_roots()}; run `make eval-fetch`")
    if path.suffix.lower() == ".pdf":
        extracted = PdfExtractor().extract(str(path))
    else:
        extracted = TextExtractor(markdown=path.suffix.lower() == ".md").extract(str(path))
    title = meta.get("title", key)
    vid = uuid.uuid4()
    return [
        EvidenceChunk(
            uuid.uuid4(),
            vid,
            title,
            1,
            d.page_start,
            d.page_end,
            tuple(d.section_path),
            d.original_text,
            tuple(d.sentences),
        )
        for d in chunk_document(extracted, title)
    ]


def _gateway(ledger: MemoryLedger) -> LlmGateway:
    s = get_settings()
    key = os.environ.get("OPENAI_API_KEY") or (
        s.openai_api_key.get_secret_value() if s.openai_api_key else ""
    )
    if not key:
        pytest.fail("live summary test needs OPENAI_API_KEY in the environment or .env")
    return LlmGateway(
        chat=OpenAIResponsesProvider(key, s.llm_timeout_s),
        prompts=default_registry(),
        prices=price_table_from_settings(s),
        ledger=ledger,
        settings=s,
    )


async def _summary(gw: LlmGateway, chunks: list[EvidenceChunk], **params: Any) -> Generated:
    by_id = {c.chunk_id: c for c in chunks}
    refs = [ChunkRef(c.chunk_id, i, len(c.original_text) // 4) for i, c in enumerate(chunks)]
    plan = plan_evidence(refs, None, 12_000)

    async def load(ids: Any) -> list[EvidenceChunk]:
        return [by_id[i] for i in ids]

    return await generate_summary(
        gw, load, owner_id=uuid.uuid4(), params={"focus": None, "tone": "neutral", **params}, plan=plan
    )


async def test_length_control() -> None:
    chunks = _chunks(DOC)
    ledger = MemoryLedger()
    gw = _gateway(ledger)
    samples: dict[str, Any] = {}
    words: dict[str, int] = {}
    for name, params in {
        "short_executive": {"length": "short", "tone": "executive"},
        "medium_focus": {"length": "medium", "focus": "payment and termination terms"},
        "long_plain": {"length": "long", "tone": "plain"},
    }.items():
        g = await _summary(gw, chunks, **params)
        words[name] = sum(word_count(s.text) for s in g.segments)
        cited = sum(s.support == "cited" for s in g.segments)
        print(f"\n[{name}] words={words[name]} segments={len(g.segments)} cited={cited} reasons={g.reasons}")
        print("  " + " ".join(s.text for s in g.segments))
        samples[name] = {"words": words[name], "cost_usd": str(g.cost_usd), "reasons": list(g.reasons)}
        assert g.segments and cited >= len(g.segments) - 1, name
        assert words[name] <= WORD_LIMIT[str(params["length"])], name
    if out := os.environ.get("SUMMARY_SAMPLES_OUT"):
        await asyncio.to_thread(pathlib.Path(out).write_text, json.dumps(samples, indent=2))
    assert words["short_executive"] <= 80  # AC6
    assert words["long_plain"] >= 150  # AC6
    assert sum((r.cost_usd for r in ledger.records), Decimal(0)) < CAP
