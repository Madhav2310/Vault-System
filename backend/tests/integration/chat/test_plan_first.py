"""Plan-first answers: a 2-part question gets a plan with both parts; a part with no evidence is stated
as not found by code. Default off: the answer prompt and schema are unchanged."""

from collections.abc import Callable
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.chat.coverage import ELEMENT_NOT_FOUND
from app.llm.providers.fake import structured
from tests.integration.chat.conftest import owners
from tests.support.ledger_env import ledger_owners
from tests.support.turn_env import EMBED_SLOT, analysis, install_runner, new_session, post, seed_chunk

__all__ = ["ledger_owners", "owners"]  # fixture imports

Q = "notice period and governing law"
BODY = "The notice period is 30 days. Either party may terminate on notice."
DIMS = ["notice period", "governing law"]
SEG = {"text": "Notice is 30 days.", "evidence": ["E1.0"]}
VERDICT = structured({"verdicts": [{"index": 0, "verdict": "supported", "reason": "ok"}]})


def _answer(plan: Any) -> Any:
    body: dict[str, Any] = {"status": "answer", "segments": [SEG], "followups": []}
    if plan is not None:
        body = {"status": "answer", "plan": plan, "segments": [SEG], "followups": []}
    return structured(body)


async def _run(
    sm: async_sessionmaker[AsyncSession],
    ledger_owners: Callable[[], Any],
    api: httpx.AsyncClient,
    plan: Any,
    **settings: Any,
) -> tuple[list[str], list[str]]:
    o = await ledger_owners()
    await seed_chunk(sm, o.id, "Alpha Agreement", BODY)
    # exactly four model steps: analysis, embedding, ONE generation (the plan is part of it), support check
    install_runner(api, sm, [analysis(Q, dimensions=DIMS), EMBED_SLOT, _answer(plan), VERDICT], **settings)
    sid = await new_session(api, o)
    r = await post(api, o, sid, Q, "m1")
    assert r.status_code == 200, r.text
    async with sm() as s:
        codes = (
            await s.execute(text("SELECT reason_codes FROM rag_runs WHERE owner_id = :o"), {"o": o.id})
        ).scalar_one()
    hist = (await api.get(f"/v1/chat/sessions/{sid}/messages", headers=o.auth)).json()["items"]
    return [s["text"] for s in hist[1]["segments"]], list(codes)


async def test_parts_covered(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    plan = [
        {"element": "R1", "handles": ["E1.0"], "not_found": False},
        {"element": "R2", "handles": [], "not_found": True},
    ]
    texts, codes = await _run(sm, ledger_owners, api, plan, chat_plan_first=True)
    assert texts[0] == "Notice is 30 days."
    assert len(texts) == 2 and "governing law" in texts[1]  # the uncovered part is stated, never dropped
    assert ELEMENT_NOT_FOUND in codes


async def test_both_parts_covered_adds_nothing(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    plan = [
        {"element": "R1", "handles": ["E1.0"], "not_found": False},
        {"element": "R2", "handles": ["E1.0"], "not_found": False},
    ]
    texts, codes = await _run(sm, ledger_owners, api, plan, chat_plan_first=True)
    assert texts == ["Notice is 30 days."] and ELEMENT_NOT_FOUND not in codes


async def test_malformed_plan_degrades_to_plain_answer(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    texts, codes = await _run(sm, ledger_owners, api, "not a list", chat_plan_first=True)
    assert texts == ["Notice is 30 days."] and ELEMENT_NOT_FOUND not in codes


async def test_default_off_ignores_plan(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    texts, codes = await _run(sm, ledger_owners, api, None)
    assert texts == ["Notice is 30 days."] and ELEMENT_NOT_FOUND not in codes
