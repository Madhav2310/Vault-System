"""Live generate-node checks (S3.08 AC5, AC6) through the real `balanced` role.

Spends real money (cap asserted: both tests together < $0.05). Run with
`uv run pytest tests/live/chat/test_generate.py -s -m live`; excluded from `make ci`. Needs the CUAD source
PDFs in `eval/sources/` (`make eval-fetch`; a worktree falls back to the main checkout's copy) and fails,
does not skip, when they or OPENAI_API_KEY are missing. Prints the answers for the eval questions only.
"""

import asyncio
import os
import pathlib
import re
import uuid
from decimal import Decimal
from functools import lru_cache
from typing import Any

import pytest
import yaml

from app.chat.evidence import EvidenceChunk, EvidenceSet, build_evidence
from app.chat.nodes.generate import GenerateResult, generate
from app.core.settings import get_settings
from app.ingestion.chunk import ChunkDraft, chunk_document
from app.ingestion.extract.pdf import PdfExtractor
from app.llm.gateway import LlmGateway, MemoryLedger
from app.llm.pricing import price_table_from_settings
from app.llm.prompts import default_registry
from app.llm.providers.openai import OpenAIEmbeddingProvider
from app.llm.providers.openai_chat import OpenAIResponsesProvider

pytestmark = pytest.mark.live

ROOT = pathlib.Path(__file__).resolve().parents[4]
DATASET = ROOT / "eval/datasets/vault_eval_v1"
CAP = Decimal("0.05")
ITEMS = ("cuad_consulting:parties", "cuad_consulting:agreement_date", "cuad_consulting:governing_law")
spent = Decimal(0)


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
        pytest.fail("live generate test needs OPENAI_API_KEY in the environment or .env")
    return LlmGateway(
        chat=OpenAIResponsesProvider(key, s.llm_timeout_s),
        embeddings=OpenAIEmbeddingProvider(key, s.llm_timeout_s),
        prompts=default_registry(),
        prices=price_table_from_settings(s),
        ledger=ledger,
        settings=s,
    )


def _norm(t: str) -> str:
    return " ".join(t.split())


@lru_cache(maxsize=1)
def _consulting() -> tuple[list[EvidenceChunk], dict[str, Any]]:
    docs = yaml.safe_load((DATASET / "documents.yaml").read_text())["documents"]
    rel = docs["cuad_consulting"]["file"]
    for root in _source_roots():
        path = root / rel
        if path.exists():
            break
    else:
        pytest.fail(f"{rel} not found under {_source_roots()}; run `make eval-fetch`")
    drafts: list[ChunkDraft] = chunk_document(PdfExtractor().extract(str(path)), "Consulting Agreement")
    vid = uuid.uuid4()
    chunks = [
        EvidenceChunk(
            uuid.uuid4(),
            vid,
            "Consulting Agreement",
            1,
            d.page_start,
            d.page_end,
            tuple(d.section_path),
            d.original_text,
            tuple(d.sentences),
        )
        for d in drafts
        if d.kind == "text"
    ]
    golden = {it["id"]: it for it in yaml.safe_load((DATASET / "golden.yaml").read_text())["items"]}
    return chunks, golden


def _select(chunks: list[EvidenceChunk], quote: str) -> list[EvidenceChunk]:
    """The gold chunk plus 3 distractors (deterministic: first chunks that do not hold the quote)."""
    q = _norm(quote)
    gold = next((c for c in chunks if q in _norm(c.original_text)), None)
    assert gold is not None, f"gold quote not found in any chunk: {q[:60]!r}"
    others = [c for c in chunks if c is not gold][::3][:3]
    return sorted([gold, *others], key=chunks.index)


def _show(r: GenerateResult, label: str) -> None:
    u = r.usage
    print(
        f"  [{label}] type={r.response_type} reasons={list(r.reasons)} tokens in={u.input_tokens} "
        f"cache_r={u.cache_read_tokens} cache_w={u.cache_write_tokens} out={u.output_tokens} "
        f"cost=${r.cost_usd}"
    )


async def test_live_citations() -> None:
    global spent
    chunks, golden = _consulting()
    gw = _gateway(MemoryLedger())
    for item_id in ITEMS:
        item = golden[item_id]
        quote = item["gold"][0]["quote"]
        ev: EvidenceSet = build_evidence(_select(chunks, quote))
        r = await generate(
            gw, owner_id=uuid.uuid4(), evidence=ev, question=item["question"], route="lookup", mode="rag"
        )
        spent += r.cost_usd
        print(f"\n{item_id}\n  Q: {item['question']}\n  A: {r.text!r}")
        for seg in r.resolution.segments:
            for ref in seg.references:
                hit = _norm(quote) in _norm(ref.cited_text) or _norm(ref.cited_text) in _norm(quote)
                page = ev.chunks[ref.chunk_id].page_start
                print(f"  cited p.{page}: {_norm(ref.cited_text)[:160]!r} gold_overlap={hit}")
        _show(r, "rag")
        assert r.response_type == "answer", (item_id, r.reasons)
        refs = [ref for seg in r.resolution.segments for ref in seg.references]
        assert refs, f"{item_id}: answered with no valid citation"
        for ref in refs:  # code-copied text equals the stored chunk text
            chunk = ev.chunks[ref.chunk_id]
            assert ref.cited_text == chunk.original_text[ref.char_start : ref.char_end]
        assert r.reasons == ()
    print(f"\nspent so far: ${spent}")
    assert spent < CAP


async def test_prompt_cache_hit() -> None:
    global spent
    chunks, _ = _consulting()
    ev = build_evidence(chunks)  # the whole document: FULL_CONTEXT style, well over 1,024 tokens
    gw = _gateway(MemoryLedger())
    runs: list[GenerateResult] = []
    for _ in range(2):
        r = await generate(
            gw,
            owner_id=uuid.uuid4(),
            evidence=ev,
            question="Which jurisdiction's law governs this agreement?",
            route="lookup",
            mode="full_context",
        )
        spent += r.cost_usd
        runs.append(r)
        _show(r, f"full_context call {len(runs)}")
        await asyncio.sleep(3)
    first, second = runs
    u1 = first.usage
    prompt_tokens = u1.input_tokens + u1.cache_read_tokens + u1.cache_write_tokens
    print(f"  prompt tokens (call 1) = {prompt_tokens}")
    print(f"  A: {second.text!r}")
    print(f"\nspent so far: ${spent}")
    assert prompt_tokens >= 1024
    assert second.usage.cache_read_tokens > 0
    assert spent < CAP
    assert re.search(r"\S", second.text)
