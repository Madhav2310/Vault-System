"""S2.06 reciprocal rank fusion: hand-computed values, single-list items, id boost, tie determinism."""

import uuid

import pytest

from app.retrieval.fusion import RRF_K, Ranked, fuse

V = uuid.UUID(int=1)


def _id(n: int) -> uuid.UUID:
    return uuid.UUID(int=1000 + n)


def _r(n: int, id_match: bool = False) -> Ranked:
    return Ranked(_id(n), V, 1.0 / n, id_match)


def test_rrf_values() -> None:
    vector = [_r(1), _r(2), _r(3), _r(4)]
    lexical = [_r(3), _r(1), _r(5)]
    fused = fuse(vector, lexical)
    expected = {
        1: 1 / 61 + 1 / 62,  # vector rank 1, lexical rank 2
        2: 1 / 62,  # vector rank 2 only
        3: 1 / 63 + 1 / 61,  # vector rank 3, lexical rank 1
        4: 1 / 64,  # vector rank 4 only
        5: 1 / 63,  # lexical rank 3 only
    }
    assert RRF_K == 60
    want = {_id(n): s for n, s in expected.items()}
    assert {f.chunk_id: f.score for f in fused} == pytest.approx(want, abs=1e-9)
    # 1/61+1/62 = 0.032522 > 1/61+1/63 = 0.032266 > 1/62 = 0.016129 > 1/63 = 0.015873 > 1/64 = 0.015625
    assert [f.chunk_id for f in fused] == [_id(1), _id(3), _id(2), _id(5), _id(4)]
    assert [f.rank for f in fused] == [1, 2, 3, 4, 5]
    by = {f.chunk_id: f for f in fused}
    assert (by[_id(1)].vector_rank, by[_id(1)].lexical_rank) == (1, 2)
    assert (by[_id(3)].vector_rank, by[_id(3)].lexical_rank) == (3, 1)
    assert (by[_id(5)].vector_rank, by[_id(5)].lexical_rank) == (None, 3)
    assert by[_id(2)].lexical_rank is None and by[_id(2)].lexical_score is None
    assert by[_id(4)].vector_score == pytest.approx(0.25)
    assert by[_id(1)].version_id == V


def test_single_list_items_kept() -> None:
    only_vec = fuse([_r(1), _r(2)], [])
    assert [f.chunk_id for f in only_vec] == [_id(1), _id(2)]
    assert all(f.lexical_rank is None for f in only_vec)
    only_lex = fuse([], [_r(7)])
    assert [f.chunk_id for f in only_lex] == [_id(7)]
    assert only_lex[0].vector_rank is None and only_lex[0].lexical_rank == 1
    assert fuse([], []) == []


def test_id_boost() -> None:
    plain = fuse([], [_r(1)])[0].score
    boosted = fuse([], [_r(1, id_match=True)])[0].score
    assert plain == pytest.approx(1 / 61, abs=1e-12)
    assert boosted == pytest.approx(2 / 61, abs=1e-12)  # the lexical list's contribution doubles
    both = fuse([_r(1)], [_r(1, id_match=True)])[0]
    assert both.score == pytest.approx(1 / 61 + 2 / 61, abs=1e-12)  # the vector contribution does not
    assert both.id_match is True
    # the boost can overturn a rank gap: id-matched lexical rank 2 (2/62) beats vector-only rank 1 (1/61)
    f = fuse([_r(1)], [_r(9), _r(2, id_match=True)])
    assert [x.chunk_id for x in f] == [_id(2), _id(1), _id(9)]  # 1 and 9 tie at 1/61; vector rank decides
    assert {x.chunk_id: x.score for x in f}[_id(2)] == pytest.approx(2 / 62)


def test_deterministic_ties() -> None:
    # A: vector 1 / lexical 2, B: vector 2 / lexical 1 -> identical scores; better vector rank wins
    a, b = _r(1), _r(2)
    first = fuse([a, b], [b, a])
    assert first[0].score == first[1].score
    assert [f.chunk_id for f in first] == [a.chunk_id, b.chunk_id]
    assert [f.chunk_id for f in fuse([a, b], [b, a])] == [f.chunk_id for f in first]
    # a vector-ranked item beats a lexical-only item with the same score (both 1/62)
    c, d = _r(3), _r(4)
    mixed = fuse([_r(8), c], [_r(9), d])
    tied = [f for f in mixed if f.chunk_id in (c.chunk_id, d.chunk_id)]
    assert tied[0].score == tied[1].score and tied[0].chunk_id == c.chunk_id
    # neither has a vector rank: lexical rank 1 (1/61) ties with an id-matched rank 62 (2/122)
    lex = [_r(100 + i) for i in range(1, 62)] + [_r(500, id_match=True)]
    top, last = lex[0], lex[-1]
    by_chunk_id = fuse([], lex)[:2]
    assert by_chunk_id[0].score == by_chunk_id[1].score
    assert [f.chunk_id for f in by_chunk_id] == sorted([top.chunk_id, last.chunk_id])
    by_ordinal = fuse([], lex, ordinals={top.chunk_id: 9, last.chunk_id: 1})[:2]
    assert [f.chunk_id for f in by_ordinal] == [last.chunk_id, top.chunk_id]  # lower ordinal first
    # input order of the two lists' equal-score items does not matter beyond rank
    assert fuse([], lex) == fuse([], list(lex))


def test_duplicate_within_list_counts_once() -> None:
    f = fuse([_r(1), _r(1), _r(2)], [])
    assert [x.chunk_id for x in f] == [_id(1), _id(2)]
    assert [x.vector_rank for x in f] == [1, 2]
    assert f[0].score == pytest.approx(1 / 61)
