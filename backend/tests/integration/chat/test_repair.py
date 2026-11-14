"""Verify-and-repair over the real turn graph: prune unneeded citations, remove unsupported segments,
regenerate once when every segment fails. support_check@2 behind `chat_support_version=2`. Fake provider."""

import uuid
from collections.abc import Callable
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.explain.templates import UNSUPPORTED_ABSTAIN_TEXT
from app.llm.providers.fake import Step, structured
from tests.support.ledger_env import ledger_owners
from tests.support.turn_env import (
    EMBED_SLOT,
    analysis,
    answer,
    install_runner,
    new_session,
    parse_sse,
    post,
    seed_chunk,
)

__all__ = ["ledger_owners"]  # fixture import

Q = "Services twelve months"
BODY = (
    "The Consultant shall provide the Services for twelve months. "
    "Either party may terminate on 30 days notice. "
    "Fees are due monthly."
)


def v2(*items: tuple[int, str, list[str]], contradicts: tuple[int, ...] = ()) -> Step:
    return structured(
        {
            "verdicts": [
                {
                    "index": i,
                    "verdict": v,
                    "contradicts": i in contradicts,
                    "needed": needed,
                    "reason": f"{v} reason",
                }
                for i, v, needed in items
            ]
        }
    )


async def run(
    sm: async_sessionmaker[AsyncSession],
    owners: Callable[[], Any],
    api: httpx.AsyncClient,
    script: list[Step],
    **settings: Any,
) -> tuple[Any, Any, uuid.UUID, dict[str, Any]]:
    o = await owners()
    await seed_chunk(sm, o.id, body=BODY)
    prov = install_runner(api, sm, [EMBED_SLOT, *script], fast_path=True, **settings)
    sid = await new_session(api, o)
    r = await post(api, o, sid, Q, "m1")
    assert r.status_code == 200, r.text
    return prov, o, sid, r.json()["message"]


async def segments(api: httpx.AsyncClient, sid: uuid.UUID, o: Any) -> list[dict[str, Any]]:
    h = (await api.get(f"/v1/chat/sessions/{sid}/messages", headers=o.auth)).json()["items"]
    out: list[dict[str, Any]] = h[1]["segments"]
    return out


async def reasons(sm: async_sessionmaker[AsyncSession], o: Any) -> list[str]:
    async with sm() as s:
        row = (
            await s.execute(text("SELECT reason_codes FROM rag_runs WHERE owner_id = :o"), {"o": o.id})
        ).scalar_one()
    return list(row)


async def test_prune_and_remove(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    prov, o, sid, msg = await run(
        sm,
        ledger_owners,
        api,
        [
            answer(("Twelve months.", ["E1.0", "E1.1", "E1.2"]), ("Ninety days notice.", ["E1.1"])),
            v2((0, "supported", ["E1.0"]), (1, "unsupported", [])),
        ],
        chat_support_version=2,
    )
    segs = await segments(api, sid, o)
    assert [s["text"] for s in segs] == ["Twelve months."]  # the unsupported segment is gone
    assert len(segs[0]["evidence"]) == 1  # 3 handles cited, 1 needed
    cited = segs[0]["evidence"][0]["cited_text"]
    assert cited == "The Consultant shall provide the Services for twelve months."
    assert segs[0]["verification"] == "supported"
    assert msg["content"] == "Twelve months."  # what is stored is what is shown
    assert len(prov.requests) == 2  # generate, one support call (fast path: no analyze): no extra model call
    assert "UNSUPPORTED_REMOVED" in await reasons(sm, o)
    assert "# Needed handles" in prov.requests[-1].system  # the registry served support_check@2


async def test_stream_tells_the_ui(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    o = await ledger_owners()
    await seed_chunk(sm, o.id, body=BODY)
    install_runner(
        api,
        sm,
        [
            EMBED_SLOT,
            answer(("Twelve months.", ["E1.0"]), ("Ninety days notice.", ["E1.1"])),
            v2((0, "supported", []), (1, "unsupported", [])),
        ],
        fast_path=True,
        chat_support_version=2,
    )
    r = await post(api, o, await new_session(api, o), Q, "m1", sse=True)
    events = parse_sse(r.text)
    ver = next(e for e in events if e["type"] == "verification")
    verdicts = {v["index"]: v["verdict"] for v in ver["verdicts"]}
    assert verdicts == {0: "supported", 1: "removed"}


async def test_contradiction_kept_and_flagged(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    _, o, sid, _ = await run(
        sm,
        ledger_owners,
        api,
        [
            answer(("Twelve months.", ["E1.0"]), ("Notice is 90 days.", ["E1.1"])),
            v2((0, "supported", []), (1, "unsupported", []), contradicts=(1,)),
        ],
        chat_support_version=2,
    )
    segs = await segments(api, sid, o)
    assert [(s["text"], s["verification"]) for s in segs] == [
        ("Twelve months.", "supported"),
        ("Notice is 90 days.", "unsupported"),
    ]
    rs = await reasons(sm, o)
    assert "UNSUPPORTED_CLAIM" in rs and "UNSUPPORTED_REMOVED" not in rs


async def test_all_fail_regenerates_once(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    prov, o, sid, msg = await run(
        sm,
        ledger_owners,
        api,
        [
            answer(("Ninety days.", ["E1.1"]), ("Paid yearly.", ["E1.2"])),
            v2((0, "unsupported", []), (1, "unsupported", [])),
            answer(("Fees are due monthly.", ["E1.2"])),
            v2((2, "supported", [])),
        ],
        chat_support_version=2,
    )
    assert len(prov.requests) == 4  # generate, check, generate once more, check
    assert msg["response_type"] == "answer" and msg["content"] == "Fees are due monthly."
    assert [s["text"] for s in await segments(api, sid, o)] == ["Fees are due monthly."]
    rs = await reasons(sm, o)
    assert "ANSWER_REGENERATED" in rs and "UNSUPPORTED_ABSTAINED" not in rs


async def test_regeneration_also_failing_abstains_warmly(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    prov, o, sid, msg = await run(
        sm,
        ledger_owners,
        api,
        [
            answer(("Ninety days.", ["E1.1"])),
            v2((0, "unsupported", [])),
            answer(("Paid yearly.", ["E1.2"])),
            v2((1, "unsupported", [])),
            # a third generation would pop past the script's end: the bound is one regeneration
        ],
        chat_support_version=2,
    )
    assert len(prov.requests) == 4
    assert msg["response_type"] == "abstain" and msg["content"] == UNSUPPORTED_ABSTAIN_TEXT
    segs = await segments(api, sid, o)
    assert [s["text"] for s in segs] == [UNSUPPORTED_ABSTAIN_TEXT] and segs[0]["support"] == "none"
    rs = await reasons(sm, o)
    assert "ANSWER_REGENERATED" in rs and "UNSUPPORTED_ABSTAINED" in rs


async def test_v1_unchanged(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    prov, o, sid, msg = await run(
        sm,
        ledger_owners,
        api,
        [
            answer(("Twelve months.", ["E1.0", "E1.1", "E1.2"]), ("Ninety days notice.", ["E1.1"])),
            structured(
                {
                    "verdicts": [
                        {"index": 0, "verdict": "supported", "reason": "ok"},
                        {"index": 1, "verdict": "unsupported", "reason": "no"},
                    ]
                }
            ),
        ],
    )
    segs = await segments(api, sid, o)
    assert [(s["verification"], len(s["evidence"])) for s in segs] == [("supported", 1), ("unsupported", 1)]
    assert len(segs) == 2 and msg["content"] == "Twelve months. Ninety days notice."
    assert len(prov.requests) == 2
    rs = await reasons(sm, o)
    assert "UNSUPPORTED_CLAIM" in rs and "UNSUPPORTED_REMOVED" not in rs


async def test_regeneration_is_bounded_by_the_turn_budget(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    """With the analysis call the qa budget (4 calls) has no room for a second check: the regenerated answer
    is shown unchecked, and no fifth call is made."""
    o = await ledger_owners()
    await seed_chunk(sm, o.id, body=BODY)
    prov = install_runner(
        api,
        sm,
        [
            analysis(Q),
            EMBED_SLOT,
            answer(("Ninety days.", ["E1.1"])),
            v2((0, "unsupported", [])),
            answer(("Fees are due monthly.", ["E1.2"])),
        ],
        chat_support_version=2,
    )
    sid = await new_session(api, o)
    r = await post(api, o, sid, Q, "m1")
    assert r.json()["message"]["content"] == "Fees are due monthly."
    assert len(prov.requests) == 4
    assert [s["verification"] for s in await segments(api, sid, o)] == ["unchecked"]
    rs = await reasons(sm, o)
    assert "ANSWER_REGENERATED" in rs and "SUPPORT_CHECK_FAILED" in rs
