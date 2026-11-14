"""Working memory window (design §8.8): newest whole turns within a token budget, plus session memory."""

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.evidence import EvidenceChunk
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


def merge_carried(
    current: Sequence[EvidenceChunk],
    carried: Sequence[EvidenceChunk],
    version_ids: set[uuid.UUID],
    *,
    limit: int,
) -> list[EvidenceChunk]:
    """`current` followed by up to `limit` previous-turn chunks that are new and inside the current scope."""
    seen = {c.chunk_id for c in current}
    added: list[EvidenceChunk] = []
    for c in carried:
        if len(added) >= limit:
            break
        if c.chunk_id in seen or c.version_id not in version_ids:
            continue
        seen.add(c.chunk_id)
        added.append(c)
    return [*current, *added]


async def previous_selected_chunk_ids(
    db: AsyncSession, owner_id: uuid.UUID, session_id: uuid.UUID, run_id: uuid.UUID, *, limit: int
) -> list[uuid.UUID]:
    """The top selected chunks of the session's latest earlier completed run, best first."""
    rows = await db.execute(
        text(
            "SELECT rc.chunk_id FROM retrieval_candidates rc WHERE rc.run_id = ("
            "SELECT r.id FROM rag_runs r WHERE r.session_id = :s AND r.owner_id = :o AND r.id <> :r "
            "AND r.status = 'completed' AND EXISTS (SELECT 1 FROM retrieval_candidates x "
            "WHERE x.run_id = r.id AND x.selected) ORDER BY r.created_at DESC LIMIT 1) "
            "AND rc.selected AND rc.expanded_from IS NULL ORDER BY rc.result_index LIMIT :n"
        ),
        {"s": session_id, "o": owner_id, "r": run_id, "n": limit},
    )
    return [r[0] for r in rows]
