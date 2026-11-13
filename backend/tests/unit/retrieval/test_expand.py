"""S2.09 neighbor expansion (pure planning part): budget and duplicates."""

import uuid

from app.retrieval.expand import ChunkInfo, plan_expansion

V = uuid.UUID(int=1)
SEC = ("A",)


def _c(ordinal: int, tokens: int = 100, section: tuple[str, ...] = SEC, version: uuid.UUID = V) -> ChunkInfo:
    return ChunkInfo(uuid.UUID(int=10_000 + ordinal + version.int * 1000), version, ordinal, section, tokens)


def test_budget_respected() -> None:
    chunks = {i: _c(i) for i in range(1, 12)}
    selected = [chunks[2], chunks[6], chunks[10]]
    neighbors = [chunks[i] for i in (1, 3, 5, 7, 9, 11)]
    for budget in (0, 250, 300, 349, 350, 450, 10_000):
        out = plan_expansion(selected, neighbors, budget)
        added = [e for e in out if e.expanded_from is not None]
        total = sum(e.token_count for e in out)
        # selected chunks are never dropped; additions only while the total stays within budget
        assert [e.chunk_id for e in out[:3]] == [c.chunk_id for c in selected]
        assert total <= max(budget, 300), (budget, total)
        assert sum(e.token_count for e in added) + 300 == total
    assert plan_expansion(selected, neighbors, 300) == plan_expansion(selected, [], 300)  # nothing fits
    assert len(plan_expansion(selected, neighbors, 450)) == 3 + 1  # exactly one 100-token neighbor fits
    # a neighbor too big for the remaining budget is skipped, a later smaller one still fits
    big, small = _c(3, tokens=500), _c(5, tokens=50)
    out = plan_expansion([_c(2), _c(6)], [big, small, _c(1, tokens=1000)], 300)
    assert [e.chunk_id for e in out if e.expanded_from] == [small.chunk_id]


def test_no_duplicates() -> None:
    c7, c8, c9 = _c(7), _c(8), _c(9)
    # 7 and 8 are both selected and each other's neighbor; 9 neighbors 8 only; 7 is repeated in the input
    out = plan_expansion([c7, c8, c7], [c7, c8, c9, c8], 10_000)
    ids = [e.chunk_id for e in out]
    assert len(ids) == len(set(ids))
    assert ids == [c7.chunk_id, c8.chunk_id, c9.chunk_id]
    assert [e.expanded_from for e in out] == [None, None, c8.chunk_id]
    # one neighbor shared by two selected chunks is added once, attributed to the first (rank order)
    c1, c3, c2 = _c(1), _c(3), _c(2)
    out = plan_expansion([c3, c1], [c2], 10_000)
    assert [e.chunk_id for e in out] == [c3.chunk_id, c1.chunk_id, c2.chunk_id]
    assert out[2].expanded_from == c3.chunk_id


def test_same_section_and_version_in_planner() -> None:
    sel = _c(7)
    other_section = _c(8, section=("B",))
    other_version = _c(6, version=uuid.UUID(int=2))
    assert plan_expansion([sel], [other_section, other_version], 10_000) == plan_expansion([sel], [], 10_000)
