"""DB loader: budget on real history, summary, entities, exclusion of current message, owner scope."""

import uuid
from collections.abc import Callable
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.chat.memory import load_memory
from app.core.ids import uuid7
from app.ingestion.chunk import count_tokens


async def _msg(s: AsyncSession, owner: uuid.UUID, sid: str, role: str, content: str) -> uuid.UUID:
    mid = uuid7()
    await s.execute(
        text(
            "INSERT INTO chat_messages (id, owner_id, session_id, role, content) VALUES (:i, :o, :s, :r, :c)"
        ),
        {"i": mid, "o": owner, "s": sid, "r": role, "c": content},
    )
    return mid


async def test_load_memory(
    api: httpx.AsyncClient, owners: Callable[[], Any], sm: async_sessionmaker[AsyncSession]
) -> None:
    o, other = await owners(), await owners()
    sid = (await api.post("/v1/chat/sessions", json={}, headers=o.auth)).json()["id"]
    body = "word " * 200
    async with sm() as s:
        for i in range(12):
            await _msg(s, o.id, sid, "user", f"question {i} {body}")
            await _msg(s, o.id, sid, "assistant", f"answer {i} {body}")
        current = await _msg(s, o.id, sid, "user", "current question")
        await s.execute(
            text(
                "UPDATE chat_sessions SET rolling_summary = 'sum', resolved_entities = '{\"it\": \"d1\"}' "
                "WHERE id = :i"
            ),
            {"i": sid},
        )
        await s.commit()
    async with sm() as s:
        mem = await load_memory(s, o.id, uuid.UUID(sid), exclude_message_id=current, budget=1000)
        foreign = await load_memory(s, other.id, uuid.UUID(sid))
    assert mem.rolling_summary == "sum" and mem.resolved_entities == {"it": "d1"}
    assert 0 < len(mem.turns) < 12
    assert all(len(t) == 2 and t[0].role == "user" and t[1].role == "assistant" for t in mem.turns)
    assert sum(count_tokens(m.content) for t in mem.turns for m in t) <= 1000
    assert mem.turns[-1][0].content.startswith("question 11")  # newest kept, current message excluded
    assert foreign.turns == [] and foreign.rolling_summary is None  # another owner sees nothing
