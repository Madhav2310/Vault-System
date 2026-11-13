"""S3.08 AC3/AC4: refusal and max_tokens handling in the generate node, with a real `llm_calls` ledger."""

import json
import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.chat.nodes.generate import (
    GENERATION_INVALID,
    STOP_MAX_TOKENS,
    STOP_REFUSAL,
    GenerateResult,
    generate,
)
from app.llm.gateway import DbLedgerWriter
from app.llm.providers.fake import FakeReply, max_tokens, refusal, structured
from tests.support.chat_gen import evidence, fake_gateway


def _answer(*segments: tuple[str, list[str]]) -> FakeReply:
    return structured({"status": "answer", "segments": [{"text": t, "evidence": h} for t, h in segments]})


async def _run(
    sm: async_sessionmaker[AsyncSession], owner: uuid.UUID, reply: FakeReply, mode: str = "rag"
) -> GenerateResult:
    gw, _ = fake_gateway([reply], ledger=DbLedgerWriter(sm))
    return await generate(
        gw,
        owner_id=owner,
        evidence=evidence(),
        question="How long?",
        route="lookup",
        mode=mode,  # type: ignore[arg-type]
    )


async def _rows(sm: async_sessionmaker[AsyncSession], owner: uuid.UUID) -> list[Any]:
    async with sm() as s:
        r = await s.execute(
            text(
                "SELECT purpose, role, prompt_id, prompt_version, status, error_type, input_tokens, "
                "output_tokens FROM llm_calls WHERE owner_id = :o ORDER BY created_at"
            ),
            {"o": owner},
        )
        return list(r.all())


async def test_stop_refusal(sm: async_sessionmaker[AsyncSession], owners: Callable[[], Any]) -> None:
    owner = (await owners()).id
    res = await _run(sm, owner, refusal())
    assert res.response_type == "error"
    assert res.reasons == (STOP_REFUSAL,)
    assert res.resolution.segments == ()
    rows = await _rows(sm, owner)
    assert len(rows) == 1
    assert (rows[0].purpose, rows[0].prompt_id, rows[0].prompt_version) == ("generate", "answer_rag", 3)
    assert (rows[0].status, rows[0].error_type, rows[0].role) == ("error", "refusal", "balanced")
    # S17.11: the refusal keeps the usage and the id of the row that was written
    assert res.usage.input_tokens == rows[0].input_tokens > 0 and res.cost_usd > 0
    assert len(res.call_ids) == 1


async def test_stop_max_tokens(sm: async_sessionmaker[AsyncSession], owners: Callable[[], Any]) -> None:
    owner = (await owners()).id
    full = json.dumps(
        {
            "status": "answer",
            "segments": [
                {"text": "Twelve months.", "evidence": ["E1.0"]},
                {"text": "Either party may end it on 30 days notice.", "evidence": ["E1.1", "E9.9"]},
                {"text": "Delaware law governs it.", "evidence": ["E2.0"]},
            ],
        }
    )
    cut = full[: full.index("Delaware") + 5]  # third segment is cut off mid-string
    res = await _run(sm, owner, max_tokens(cut))
    assert res.response_type == "answer"
    assert STOP_MAX_TOKENS in res.reasons
    assert [s.text for s in res.resolution.segments] == [
        "Twelve months.",
        "Either party may end it on 30 days notice.",
    ]
    assert "CITATION_UNRESOLVED" in res.reasons  # E9.9 dropped by the resolver, still reported
    cited = res.resolution.segments[0].references[0].cited_text
    assert cited == "The Consultant shall provide the Services for twelve months."
    rows = await _rows(sm, owner)
    assert [(r.status, r.error_type) for r in rows] == [("error", "max_tokens")]
    assert res.usage.input_tokens == rows[0].input_tokens > 0 and len(res.call_ids) == 1  # S17.11


async def test_stop_max_tokens_nothing_complete(
    sm: async_sessionmaker[AsyncSession], owners: Callable[[], Any]
) -> None:
    owner = (await owners()).id
    res = await _run(sm, owner, max_tokens('{"status": "answer", "segments": [{"text": "Twelve mon'))
    assert (res.response_type, res.reasons) == ("error", (STOP_MAX_TOKENS,))
    assert res.resolution.segments == ()
    assert len(await _rows(sm, owner)) == 1


async def test_abstain(sm: async_sessionmaker[AsyncSession], owners: Callable[[], Any]) -> None:
    owner = (await owners()).id
    res = await _run(sm, owner, structured({"status": "abstain", "segments": []}))
    assert (res.response_type, res.reasons) == ("abstain", ())
    assert res.resolution.segments == ()
    assert res.usage.input_tokens == 100 and len(res.call_ids) == 1
    rows = await _rows(sm, owner)
    assert [(r.status, r.error_type) for r in rows] == [("ok", None)]


async def test_unresolved_handle_dropped_in_full_context(
    sm: async_sessionmaker[AsyncSession], owners: Callable[[], Any]
) -> None:
    owner = (await owners()).id
    reply = _answer(("Twelve months.", ["E1.0", "E7.3"]), ("Invented.", ["E5.0"]))
    res = await _run(sm, owner, reply, mode="full_context")
    assert res.response_type == "answer"
    assert res.reasons == ("CITATION_UNRESOLVED",)
    first, second = res.resolution.segments
    assert first.support == "cited" and first.dropped_handles == ("E7.3",)
    assert second.support == "none" and second.dropped_handles == ("E5.0",)


async def test_schema_invalid_reply(sm: async_sessionmaker[AsyncSession], owners: Callable[[], Any]) -> None:
    owner = (await owners()).id
    # RAG mode: a handle outside the enum fails the strict schema
    res = await _run(sm, owner, _answer(("Twelve months.", ["E9.9"])))
    assert (res.response_type, res.reasons) == ("error", (GENERATION_INVALID,))
    assert len(res.call_ids) == 1 and res.usage.input_tokens == 100  # cost of the bad reply is kept
    # not JSON at all
    res = await _run(sm, owner, FakeReply())
    assert (res.response_type, res.reasons) == ("error", (GENERATION_INVALID,))
    assert [r.status for r in await _rows(sm, owner)] == ["ok", "ok"]
