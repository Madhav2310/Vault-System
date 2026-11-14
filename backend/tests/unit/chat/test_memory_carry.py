"""Carry-forward of the previous turn's evidence into a follow-up (CHAT_MEMORY_CARRY_EVIDENCE)."""

import uuid

from app.chat.evidence import EvidenceChunk
from app.chat.memory import merge_carried


def chunk(n: int, version: uuid.UUID) -> EvidenceChunk:
    return EvidenceChunk(
        uuid.UUID(int=n), version, f"doc{n}", 1, 1, 1, (), f"text {n}", ((0, 6),), uuid.UUID(int=1000 + n)
    )


V1, V2 = uuid.UUID(int=901), uuid.UUID(int=902)


def ids(chunks: list[EvidenceChunk]) -> list[int]:
    return [c.chunk_id.int for c in chunks]


def test_carried_chunks_follow_current_evidence_in_order() -> None:
    cur, prior = [chunk(1, V1), chunk(2, V1)], [chunk(3, V1), chunk(4, V1)]
    assert merge_carried(cur, prior, {V1}, limit=4) == cur + prior


def test_chunk_already_in_current_evidence_is_not_duplicated() -> None:
    cur, prior = [chunk(1, V1), chunk(2, V1)], [chunk(2, V1), chunk(3, V1)]
    assert ids(merge_carried(cur, prior, {V1}, limit=4)) == [1, 2, 3]


def test_carried_chunk_outside_current_scope_is_dropped() -> None:
    cur, prior = [chunk(1, V1)], [chunk(2, V2), chunk(3, V1)]
    assert ids(merge_carried(cur, prior, {V1}, limit=4)) == [1, 3]


def test_limit_counts_only_added_chunks() -> None:
    cur, prior = [chunk(1, V1)], [chunk(2, V1), chunk(3, V1), chunk(4, V1)]
    assert ids(merge_carried(cur, prior, {V1}, limit=2)) == [1, 2, 3]


def test_zero_limit_or_nothing_prior_changes_nothing() -> None:
    cur = [chunk(1, V1)]
    assert merge_carried(cur, [chunk(2, V1)], {V1}, limit=0) == cur
    assert merge_carried(cur, [], {V1}, limit=4) == cur


def test_turn_state_remembers_the_scope_before_narrowing() -> None:
    from app.chat.graph import TurnState

    assert TurnState.__dataclass_fields__["scope_unnarrowed"].default is None
