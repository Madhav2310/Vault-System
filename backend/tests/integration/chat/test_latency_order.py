"""Latency order: cache-friendly request prefixes (static first) and segments streamed before the
support check is called (verdict labels arrive after). Fake provider only."""

import json
import uuid
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.chat.evidence import cache_mode_for
from app.chat.memory import Memory, Msg
from app.chat.nodes import run_generate
from app.chat.nodes.analyze import ScopeDoc, analyze_query
from app.chat.nodes.generate import compose_user_message, generate, history_block
from app.llm.providers.fake import structured
from tests.support.chat_gen import evidence, fake_gateway
from tests.support.ledger_env import ledger_owners
from tests.support.turn_env import EMBED_SLOT, analysis, answer, install_runner, new_session, post, seed_chunk

__all__ = ["ledger_owners"]  # fixture import

OWNER = uuid.uuid4()
MEM = Memory(turns=[[Msg("user", "hello"), Msg("assistant", "hi there")]], rolling_summary="earlier talk")
OUT = {
    "standalone_query": "q",
    "target_refs": [],
    "dimensions": [],
    "intent": "qa",
    "needs_whole_document": False,
    "clarification": None,
}


def _common_prefix(a: str, b: str) -> str:
    n = 0
    while n < min(len(a), len(b)) and a[n] == b[n]:
        n += 1
    return a[:n]


async def _answer_request(question: str) -> Any:
    gw, fake = fake_gateway([answer(("Twelve months.", ["E1.0"]))])
    await generate(gw, owner_id=OWNER, evidence=evidence(), question=question, route="qa", memory=MEM)
    return fake.requests[0]


async def test_answer_prefix_is_byte_identical_across_questions() -> None:
    a = await _answer_request("How long is the term?")
    b = await _answer_request("What is the governing law?")
    assert a.system == b.system and a.output_schema == b.output_schema  # static: prompt + schema
    assert a.cache_mode == b.cache_mode == cache_mode_for("rag")
    ca, cb = a.messages[0]["content"], b.messages[0]["content"]
    assert ca != cb
    static = evidence().text + "\n\n" + history_block(MEM)
    assert ca.startswith(static) and _common_prefix(ca, cb).startswith(static)
    assert ca.rindex("<question>") > len(static)  # per-question text is only at the end


def test_compose_puts_question_last() -> None:
    msg = compose_user_message(evidence(), "Q?", "qa", MEM, coverage="c", conflicts="x")
    assert msg.rstrip().endswith("</question>")
    assert msg.index("<question>") > max(msg.index(t) for t in ("<route>", "<coverage>", "<conflicts>"))


async def test_analyze_prefix_is_byte_identical_across_questions() -> None:
    docs = [ScopeDoc(uuid.uuid4(), "Medalist consulting agreement"), ScopeDoc(uuid.uuid4(), "Pey Dirt")]
    gw, fake = fake_gateway([structured(OUT), structured(OUT)])
    for q in ("When does it expire?", "Who are the parties?"):
        await analyze_query(gw, question=q, memory=MEM, documents=docs, owner_id=OWNER)
    a, b = fake.requests
    assert a.system == b.system and a.output_schema == b.output_schema
    ca, cb = a.messages[0]["content"], b.messages[0]["content"]
    prefix = _common_prefix(ca, cb)
    for key in ('"documents"', '"history"', '"rolling_summary"', '"resolved_entities"'):
        assert key in prefix
    assert "Medalist" in prefix and "expire" not in prefix and "parties" not in prefix


async def test_segments_stream_before_the_support_check_call(
    sm: async_sessionmaker[AsyncSession],
    ledger_owners: Callable[[], Any],
    api: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`verify` is a node after `generate`: every segment is emitted while generating, so the support-check
    model call is made only after the last segment event, and its verdict labels come last."""
    o = await ledger_owners()
    await seed_chunk(sm, o.id, "Services Agreement")
    timeline: list[str] = []
    real_emit = run_generate.emit_segment

    async def emit(ctx: Any, seg: Any, ev: Any) -> None:
        timeline.append("segment")
        await real_emit(ctx, seg, ev)

    monkeypatch.setattr(run_generate, "emit_segment", emit)
    verdicts = structured(
        {"verdicts": [{"index": i, "verdict": "supported", "reason": "ok"} for i in (0, 1)]}
    )
    two = answer(("Twelve months.", ["E1.0"]), ("Thirty days notice.", ["E1.1"]))
    prov = install_runner(api, sm, [analysis("Services twelve months"), EMBED_SLOT, two, verdicts])
    real_complete, real_stream = prov.complete, prov.stream

    async def complete(req: Any) -> Any:
        timeline.append("support_check" if "verdicts" in json.dumps(req.output_schema) else "analyze")
        return await real_complete(req)

    def stream(req: Any) -> Any:
        timeline.append("generate")
        return real_stream(req)

    monkeypatch.setattr(prov, "complete", complete)
    monkeypatch.setattr(prov, "stream", stream)
    sid = await new_session(api, o)
    r = await post(api, o, sid, "Services twelve months", "m1", sse=True)
    assert r.status_code == 200
    assert timeline.count("segment") == 2 and timeline.count("support_check") == 1
    assert timeline.index("generate") < timeline.index("segment")
    assert max(i for i, t in enumerate(timeline) if t == "segment") < timeline.index("support_check")
    types = [json.loads(ln[5:])["type"] for ln in r.text.splitlines() if ln.startswith("data:")]
    assert types.index("segment") < types.index("verification")
