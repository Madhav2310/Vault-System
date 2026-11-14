"""Typed calculation segments through a whole turn: the persisted and streamed segment is a normal text
segment whose number code computed from the cited sentences; operands are ordinary citations. Default off:
the prompt and schema are unchanged. No SSE or frontend change."""

from collections.abc import Callable
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.chat.calc import CALC_CORRECTED, CALC_OPERAND_UNVERIFIED
from app.llm.providers.fake import structured
from tests.integration.chat.conftest import owners
from tests.support.ledger_env import ledger_owners
from tests.support.turn_env import EMBED_SLOT, analysis, install_runner, new_session, post, seed_chunk

__all__ = ["ledger_owners", "owners"]  # fixture imports

Q = "total revenue for 2023 and 2024"
BODY = "Revenue was $1,200 in 2023. Revenue was $1,500 in 2024."
VERDICT = structured({"verdicts": [{"index": 0, "verdict": "supported", "reason": "ok"}]})


def _answer(operands: list[dict[str, str]], calc_on: bool = True) -> Any:
    seg: dict[str, Any] = {"text": "Total revenue was $1,600.", "evidence": ["E1.0"]}
    if calc_on:
        seg["calc"] = {"op": "add", "operands": operands, "unit": "", "stated_result": "$1,600"}
    return structured({"status": "answer", "segments": [seg], "followups": []})


OK = [{"handle": "E1.0", "value_text": "$1,200"}, {"handle": "E1.1", "value_text": "$1,500"}]
BAD = [{"handle": "E1.0", "value_text": "$1,200"}, {"handle": "E1.0", "value_text": "$1,500"}]


async def _run(
    sm: async_sessionmaker[AsyncSession],
    ledger_owners: Callable[[], Any],
    api: httpx.AsyncClient,
    answer: Any,
    **settings: Any,
) -> tuple[list[dict[str, Any]], list[str], str]:
    o = await ledger_owners()
    await seed_chunk(sm, o.id, "Alpha Agreement", BODY)
    install_runner(api, sm, [analysis(Q), EMBED_SLOT, answer, VERDICT], **settings)
    sid = await new_session(api, o)
    r = await post(api, o, sid, Q, "m1", sse=True)
    assert r.status_code == 200, r.text
    async with sm() as s:
        codes = (
            await s.execute(text("SELECT reason_codes FROM rag_runs WHERE owner_id = :o"), {"o": o.id})
        ).scalar_one()
    hist = (await api.get(f"/v1/chat/sessions/{sid}/messages", headers=o.auth)).json()["items"]
    return hist[1]["segments"], list(codes), r.text


async def test_calculation_corrected_and_cited(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    segs, codes, stream = await _run(sm, ledger_owners, api, _answer(OK), chat_calc=True)
    assert [s["text"] for s in segs] == ["Adding $1,200 and $1,500 gives $2,700."]
    assert CALC_CORRECTED in codes
    assert segs[0]["evidence"], "operand sentences are ordinary citations"
    assert "$2,700" in stream and "$1,600" not in stream  # the streamed segment is the checked one too


async def test_unverified_operand_is_plain_and_uncited(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    segs, codes, _ = await _run(sm, ledger_owners, api, _answer(BAD), chat_calc=True)
    assert [s["text"] for s in segs] == ["Total revenue was $1,600."]
    assert CALC_OPERAND_UNVERIFIED in codes and not segs[0]["evidence"]


async def test_default_off_ignores_calc(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    segs, codes, _ = await _run(sm, ledger_owners, api, _answer(OK, calc_on=False))
    assert [s["text"] for s in segs] == ["Total revenue was $1,600."]
    assert CALC_CORRECTED not in codes and CALC_OPERAND_UNVERIFIED not in codes
