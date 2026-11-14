"""Working memory window (design §8.8): newest whole turns within a token budget, plus session memory."""

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.chunk import count_tokens

WINDOW_TOKENS = 3000
_FETCH_LIMIT = 200


@dataclass(frozen=True)
class Msg:
    role: str
    content: str


@dataclass(frozen=True)
class Memory:
    turns: list[list[Msg]]  # oldest first; each turn = a user message and the replies that follow it
    rolling_summary: str | None = None
    resolved_entities: dict[str, Any] = field(default_factory=dict)


def group_turns(messages: Sequence[Msg]) -> list[list[Msg]]:
    """Chronological messages -> turns. A turn starts at a user message; leading non-user messages form one
    orphan group (only possible when the history was cut)."""
    turns: list[list[Msg]] = []
    for m in messages:
        if m.role == "user" or not turns:
            turns.append([m])
        else:
            turns[-1].append(m)
    return turns


def select_window(
    turns: Sequence[Sequence[Msg]],
    budget: int = WINDOW_TOKENS,
    count: Callable[[str], int] = count_tokens,
) -> list[list[Msg]]:
    """Newest turns whose total tokens fit `budget`. Whole turns only: stops at the first turn that does not
    fit (older ones are not used to fill the gap), so a turn that alone exceeds the budget yields nothing."""
    kept: list[list[Msg]] = []
    used = 0
    for turn in reversed(turns):
        cost = sum(count(m.content) for m in turn)
        if used + cost > budget:
            break
        used += cost
        kept.append(list(turn))
    kept.reverse()
    return kept


async def load_memory(
    db: AsyncSession,
    owner_id: uuid.UUID,
    session_id: uuid.UUID,
    *,
    exclude_message_id: uuid.UUID | None = None,
    budget: int = WINDOW_TOKENS,
) -> Memory:
    """Recent turns (excluding the message being answered) + rolling summary and resolved entities."""
    s = (
        (
            await db.execute(
                text(
                    "SELECT rolling_summary, resolved_entities FROM chat_sessions "
                    "WHERE id = :s AND owner_id = :o"
                ),
                {"s": session_id, "o": owner_id},
            )
        )
        .mappings()
        .first()
    )
    if s is None:
        return Memory([])
    rows = (
        await db.execute(
            text(
                "SELECT role, content FROM chat_messages WHERE session_id = :s AND owner_id = :o "
                "AND (CAST(:x AS uuid) IS NULL OR id <> :x) "
                "AND (run_id IS NULL OR run_id NOT IN (SELECT CAST(r.analysis->>'supersedes' AS uuid) "
                "FROM rag_runs r WHERE r.session_id = :s AND r.owner_id = :o AND r.status = 'completed' "
                "AND r.analysis->>'supersedes' IS NOT NULL)) ORDER BY created_at DESC, id DESC LIMIT :n"
            ),
            {"s": session_id, "o": owner_id, "x": exclude_message_id, "n": _FETCH_LIMIT},
        )
    ).all()
    turns = group_turns([Msg(r[0], r[1]) for r in reversed(rows)])
    if len(rows) == _FETCH_LIMIT and turns and turns[0][0].role != "user":
        turns = turns[1:]  # the fetch cut this turn in half
    return Memory(select_window(turns, budget), s["rolling_summary"], dict(s["resolved_entities"] or {}))
