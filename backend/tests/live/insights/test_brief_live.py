"""Live brief sample: three corpus documents through the real `balanced` / `fast` roles.

Spends real money (asserted: all three documents together < $0.10, each capped by `max_usd`). Run with
`uv run pytest tests/live/insights/test_brief_live.py -s -m live`; excluded from `make ci`. Needs the corpus
sources in `eval/sources/` (`make eval-fetch`; a worktree falls back to the main checkout's copy) and fails,
does not skip, when they or OPENAI_API_KEY are missing. Set `BRIEF_SAMPLES_OUT=<path>` to also write the
briefs (public corpus documents only) as JSON for `eval/baselines/brief_samples.json`.
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
from app.insights.stage import ChunkRef, Generated, generate_brief, plan_groups
from app.llm.gateway import LlmGateway, MemoryLedger
from app.llm.pricing import price_table_from_settings
from app.llm.prompts import default_registry
from app.llm.providers.openai import OpenAIEmbeddingProvider
from app.llm.providers.openai_chat import OpenAIResponsesProvider

pytestmark = pytest.mark.live

ROOT = pathlib.Path(__file__).resolve().parents[4]
DATASET = ROOT / "eval/datasets/vault_eval_v1"
CAP = Decimal("0.10")
PER_DOC = Decimal("0.04")
MIN_VERIFIED = 0.80
DOCS = {  # key -> expected category
    "cuad_consulting": "contract",  # CUAD
    "fb_ulta_q4": "financial_report",  # FinanceBench
    "auth_travel_policy": "policy",  # authored (carries an injection attempt)
}


def _source_roots() -> list[pathlib.Path]:
    roots = [ROOT]
    parts = ROOT.parts
    if ".claude" in parts:  # worktree: .../<repo>/.claude/worktrees/<name>
        roots.append(pathlib.Path(*parts[: parts.index(".claude")]))
    return roots


def _gateway(ledger: MemoryLedger) -> LlmGateway:
    s = get_settings()
    key = os.environ.get("OPENAI_API_KEY") or (
        s.openai_api_key.get_secret_value() if s.openai_api_key else ""
    )
    if not key:
        pytest.fail("live brief test needs OPENAI_API_KEY in the environment or .env")
    return LlmGateway(
        chat=OpenAIResponsesProvider(key, s.llm_timeout_s),
        embeddings=OpenAIEmbeddingProvider(key, s.llm_timeout_s),
        prompts=default_registry(),
        prices=price_table_from_settings(s),
        ledger=ledger,
        settings=s,
    )


def _chunks(key: str) -> list[EvidenceChunk]:
    meta = yaml.safe_load((DATASET / "documents.yaml").read_text())["documents"][key]
    rel = meta["file"]
    path = next((r / rel for r in _source_roots() if (r / rel).is_file()), None)
    if path is None:
        pytest.fail(f"{rel} not found under {_source_roots()}; run `make eval-fetch`")
    if path.suffix.lower() == ".pdf":
        extracted = PdfExtractor().extract(str(path))
    else:
        extracted = TextExtractor(markdown=path.suffix.lower() == ".md").extract(str(path))
    vid = uuid.uuid4()
    return [
        EvidenceChunk(
            uuid.uuid4(),
            vid,
            meta.get("title", key),
            1,
            d.page_start,
            d.page_end,
            tuple(d.section_path),
            d.original_text,
            tuple(d.sentences),
        )
        for d in chunk_document(extracted, meta.get("title", key))
        if d.kind == "text"
    ]


async def _brief(gw: LlmGateway, chunks: list[EvidenceChunk]) -> Generated:
    by_id = {c.chunk_id: c for c in chunks}
    # token_count is not on EvidenceChunk: approximate from characters (1 token ~ 4 chars)
    refs = [ChunkRef(c.chunk_id, len(c.original_text) // 4, c.section_path) for c in chunks]
    groups = plan_groups(refs, get_settings().brief_max_evidence_tokens)

    async def load(ids: Any) -> list[EvidenceChunk]:
        return [by_id[i] for i in ids]

    return await generate_brief(
        gw,
        load,
        owner_id=uuid.uuid4(),
        version_id=chunks[0].version_id,
        job_id=None,
        groups=groups,
        max_usd=PER_DOC,
    )


async def test_three_docs() -> None:
    ledger = MemoryLedger()
    gw = _gateway(ledger)
    total = Decimal(0)
    facts = verified = 0
    samples: dict[str, Any] = {}
    for key, expected in DOCS.items():
        gen = await _brief(gw, _chunks(key))
        b = gen.brief
        total += gen.cost_usd
        nv = sum(f.status == "verified" for f in b.facts)
        facts += len(b.facts)
        verified += nv
        print(
            f"\n[{key}] category={b.category} (expected {expected}) type={b.document_type!r} "
            f"facts={len(b.facts)} verified={nv} partial={b.partial} reasons={b.reasons} "
            f"calls={gen.calls} cost=${gen.cost_usd} model={gen.model}\n  tags={b.tags}\n"
            f"  summary={b.summary!r}"
        )
        for f in b.facts[:6]:
            print(f"  - {f.field}: {f.value!r} [{f.status}{'/' + f.reason if f.reason else ''}]")
        samples[key] = {"cost_usd": str(gen.cost_usd), "model": gen.model, **b.model_dump(mode="json")}
        assert b.category == expected, key
        assert len(b.facts) >= 3 and 3 <= len(b.suggested_questions) <= 6, key
    ratio = verified / max(facts, 1)
    print(f"\nTOTAL cost=${total}; verified {verified}/{facts} = {ratio:.0%}")
    if out := os.environ.get("BRIEF_SAMPLES_OUT"):
        await asyncio.to_thread(pathlib.Path(out).write_text, json.dumps(samples, indent=2))
    assert total < CAP
    assert ratio >= MIN_VERIFIED
