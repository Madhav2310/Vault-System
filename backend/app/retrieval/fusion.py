"""Reciprocal rank fusion of the vector and lexical lists.

`score = sum over lists of weight / (RRF_K + rank)`, rank 1-based within each list; a lexical hit with
`id_match` carries weight 2 (exact identifier boost). Ties break by vector rank (the vector list is ordered by
distance), then chunk ordinal (when the caller supplies it), then chunk id, so the order is deterministic.

Fusion works over a small `Ranked` record so it does not depend on the hit types of the vector and lexical
search modules. Pure code: no I/O, no model, no document text.
"""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

RRF_K = 60
ID_MATCH_WEIGHT = 2.0


@dataclass(frozen=True)
class Ranked:
    """One entry of an already ordered (best first) result list."""

    chunk_id: uuid.UUID
    version_id: uuid.UUID
    score: float
    id_match: bool = False


@dataclass(frozen=True)
class FusedCandidate:
    """A fused candidate and its provenance (which lists ranked it, and where)."""

    chunk_id: uuid.UUID
    version_id: uuid.UUID
    rank: int  # 1-based position in the fused order
    score: float  # RRF score
    vector_rank: int | None
    lexical_rank: int | None
    vector_score: float | None
    lexical_score: float | None
    id_match: bool


@dataclass
class _Acc:
    version_id: uuid.UUID
    score: float = 0.0
    id_match: bool = False
    vector_rank: int | None = None
    lexical_rank: int | None = None
    vector_score: float | None = None
    lexical_score: float | None = None


def fuse(
    vector: Sequence[Ranked],
    lexical: Sequence[Ranked],
    *,
    ordinals: Mapping[uuid.UUID, int] | None = None,
    k: int = RRF_K,
) -> list[FusedCandidate]:
    """Fuse two best-first lists. A chunk repeated within one list counts once, at its first position."""
    ords = ordinals or {}
    acc: dict[uuid.UUID, _Acc] = {}

    for items, is_lexical in ((vector, False), (lexical, True)):
        rank = 0
        for h in items:
            a = acc.setdefault(h.chunk_id, _Acc(h.version_id))
            if (a.lexical_rank if is_lexical else a.vector_rank) is not None:
                continue  # duplicate within the same list
            rank += 1
            boosted = is_lexical and h.id_match
            a.score += (ID_MATCH_WEIGHT if boosted else 1.0) / (k + rank)
            a.id_match = a.id_match or boosted
            if is_lexical:
                a.lexical_rank, a.lexical_score = rank, h.score
            else:
                a.vector_rank, a.vector_score = rank, h.score

    def key(cid: uuid.UUID) -> tuple[float, int, int, int, uuid.UUID]:
        a = acc[cid]
        vr = a.vector_rank
        return (-a.score, 1 if vr is None else 0, vr or 0, ords.get(cid, 0), cid)

    return [
        FusedCandidate(
            chunk_id=cid,
            version_id=acc[cid].version_id,
            rank=i,
            score=acc[cid].score,
            vector_rank=acc[cid].vector_rank,
            lexical_rank=acc[cid].lexical_rank,
            vector_score=acc[cid].vector_score,
            lexical_score=acc[cid].lexical_score,
            id_match=acc[cid].id_match,
        )
        for i, cid in enumerate(sorted(acc, key=key), start=1)
    ]
