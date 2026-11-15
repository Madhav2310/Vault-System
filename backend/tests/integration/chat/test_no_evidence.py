"""Documents in scope but nothing retrieved: the turn abstains with a template, and no answer is generated."""

from collections.abc import Callable
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.explain.templates import NO_EVIDENCE_TEXT
from tests.support.docs import seed_document
from tests.support.ledger_env import ledger_owners
from tests.support.turn_env import EMBED_SLOT, analysis, install_runner, new_session, parse_sse, post

__all__ = ["ledger_owners"]  # fixture import


async def test_no_retrieved_chunk_abstains_without_generating(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    o = await ledger_owners()
    await seed_document(sm, o.id, title="Empty")  # ready, in scope, no chunks
    prov = install_runner(api, sm, [analysis("anything"), EMBED_SLOT], fast_path=False)
    r = await post(api, o, await new_session(api, o), "anything", "m1", sse=True)
    events = parse_sse(r.text)
    assert r.status_code == 200
    assert len(prov.requests) == 1  # the analysis only: no generate call, no support check
    assert [e["stage"] for e in events if e["type"] == "status"] == [
        "analyzing", "retrieving", "writing", "saving",
    ]  # fmt: skip
    assert [e["text"] for e in events if e["type"] == "segment"] == [NO_EVIDENCE_TEXT]
    async with sm() as s:
        run = (
            await s.execute(text("SELECT id, reason_codes FROM rag_runs WHERE owner_id = :o"), {"o": o.id})
        ).one()
        nodes = (
            (
                await s.execute(
                    text("SELECT node FROM run_steps WHERE run_id = :r ORDER BY seq"), {"r": run.id}
                )
            )
            .scalars()
            .all()
        )
        msg = (
            await s.execute(
                text(
                    "SELECT response_type, content FROM chat_messages "
                    "WHERE run_id = :r AND role = 'assistant'"
                ),
                {"r": run.id},
            )
        ).one()
    assert list(run.reason_codes) == ["NO_EVIDENCE"]
    assert nodes == [
        "load_context", "route_question", "analyze", "plan_context", "retrieve", "abstain_no_evidence",
    ]  # fmt: skip
    assert msg.response_type == "abstain" and msg.content == NO_EVIDENCE_TEXT
