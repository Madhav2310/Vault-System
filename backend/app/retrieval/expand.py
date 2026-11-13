"""Neighbor expansion (design 7.3): handle answers split across adjacent chunks.

For each selected chunk, in rank order, add the adjacent chunk (ordinal +1, then -1) of the same version and
the same section while the total token count stays within `max_evidence_tokens`. Selected chunks are never
dropped, even when they alone exceed the budget (then nothing is added). Every added chunk records the
selected chunk it was `expanded_from`. The database read is owner-filtered; the planning is pure.
"""

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class ChunkInfo:
    chunk_id: uuid.UUID
    version_id: uuid.UUID
    ordinal: int
    section_path: tuple[str, ...]
    token_count: int


@dataclass(frozen=True)
class ExpandedChunk:
    chunk_id: uuid.UUID
    version_id: uuid.UUID
    ordinal: int
    section_path: tuple[str, ...]
    token_count: int
    expanded_from: uuid.UUID | None  # None for a selected chunk


def _as_expanded(c: ChunkInfo, expanded_from: uuid.UUID | None) -> ExpandedChunk:
    return ExpandedChunk(c.chunk_id, c.version_id, c.ordinal, c.section_path, c.token_count, expanded_from)


def plan_expansion(
    selected: Sequence[ChunkInfo], candidates: Iterable[ChunkInfo], max_evidence_tokens: int
) -> list[ExpandedChunk]:
    """Selected chunks (rank order, duplicates removed) followed by the neighbors added, in the order added.

    `candidates` are the chunks that may be added; only those adjacent (ordinal +-1) to a selected chunk in
    the same version and the same section are used.
    """
    by_pos = {(c.version_id, c.ordinal): c for c in candidates}
    out: list[ExpandedChunk] = []
    taken: set[uuid.UUID] = set()
    for c in selected:
        if c.chunk_id not in taken:
            taken.add(c.chunk_id)
            out.append(_as_expanded(c, None))
    total = sum(e.token_count for e in out)
    for sel in list(out):
        for delta in (1, -1):
            n = by_pos.get((sel.version_id, sel.ordinal + delta))
            if n is None or n.chunk_id in taken or n.section_path != sel.section_path:
                continue
            if total + n.token_count > max_evidence_tokens:
                continue
            taken.add(n.chunk_id)
            total += n.token_count
            out.append(_as_expanded(n, sel.chunk_id))
    return out


_SELECTED = (
    "SELECT id, version_id, ordinal, section_path, token_count FROM chunks "
    "WHERE owner_id = :o AND id = ANY(CAST(:ids AS uuid[]))"
)
_NEIGHBORS = (
    "SELECT n.id, n.version_id, n.ordinal, n.section_path, n.token_count FROM chunks s "
    "JOIN chunks n ON n.owner_id = s.owner_id AND n.version_id = s.version_id "
    "AND n.ordinal IN (s.ordinal - 1, s.ordinal + 1) "
    "WHERE s.owner_id = :o AND s.id = ANY(CAST(:ids AS uuid[]))"
)


def _info(row: Sequence[Any]) -> ChunkInfo:
    return ChunkInfo(row[0], row[1], row[2], tuple(row[3] or ()), row[4])


async def expand_neighbors(
    session: AsyncSession, owner_id: uuid.UUID, selected_ids: Sequence[uuid.UUID], max_evidence_tokens: int
) -> list[ExpandedChunk]:
    """Expand the selected chunk ids (rank order). Ids the owner does not own are silently dropped."""
    ids = list(dict.fromkeys(selected_ids))
    if not ids:
        return []
    params = {"o": owner_id, "ids": ids}
    sel_rows = (await session.execute(text(_SELECTED), params)).all()
    info = {r[0]: _info(r) for r in sel_rows}
    selected = [info[i] for i in ids if i in info]
    neighbors = [_info(r) for r in (await session.execute(text(_NEIGHBORS), params)).all()]
    return plan_expansion(selected, neighbors, max_evidence_tokens)
