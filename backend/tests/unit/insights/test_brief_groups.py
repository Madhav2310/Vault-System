import uuid

from app.ingestion import status as vs
from app.insights.brief import EMPTY_PARAMS_HASH, params_hash
from app.insights.stage import ChunkRef, plan_groups


def refs(*spec: tuple[str, int]) -> list[ChunkRef]:
    return [ChunkRef(uuid.uuid4(), tokens, (section,) if section else ()) for section, tokens in spec]


def shape(chunks: list[ChunkRef], budget: int) -> list[int]:
    by_id = {c.id: i for i, c in enumerate(chunks)}
    groups = plan_groups(chunks, budget)
    assert [by_id[i] for g in groups for i in g] == list(range(len(chunks))), "order kept, nothing lost"
    return [len(g) for g in groups]


def test_everything_fitting_is_one_group() -> None:
    assert shape(refs(("A", 40), ("B", 40), ("C", 20)), 100) == [3]
    assert plan_groups([], 100) == []


def test_whole_sections_are_packed_up_to_the_budget() -> None:
    c = refs(("A", 40), ("A", 30), ("B", 40), ("C", 50), ("C", 40))
    assert shape(c, 130) == [3, 2]  # A(70)+B(40) fit; C(90) does not fit beside them
    assert shape(c, 100) == [2, 1, 2]  # A(70)+B(40) > 100: B starts a group, C(90) cannot join it
    assert shape(c, 80) == [2, 1, 1, 1]  # C(90) exceeds the budget: split between its two chunks


def test_an_oversized_section_is_split_between_chunks() -> None:
    c = refs(("A", 60), ("A", 60), ("A", 60), ("B", 30))
    # the remainder of A (60) can share a group with B (30)
    assert shape(c, 100) == [1, 1, 2]
    # a single chunk larger than the budget gets a group of its own
    assert shape(refs(("A", 500), ("B", 10)), 100) == [1, 1]


def test_flow_documents_without_sections_split_by_chunk() -> None:
    assert shape(refs(("", 40), ("", 40), ("", 40), ("", 40)), 100) == [2, 2]


def test_params_hash_is_stable() -> None:
    assert params_hash({}) == EMPTY_PARAMS_HASH == params_hash({})
    assert params_hash({"a": 1, "b": 2}) == params_hash({"b": 2, "a": 1})
    assert params_hash({"a": 1}) != EMPTY_PARAMS_HASH


def test_ready_to_enriched_is_a_legal_edge_and_only_from_ready() -> None:
    vs.check_transition(vs.READY, vs.ENRICHED)
    for src in (vs.PROCESSING, vs.UPLOADED, vs.FAILED, vs.NEEDS_OCR, vs.SUPERSEDED):
        try:
            vs.check_transition(src, vs.ENRICHED)
        except vs.IllegalTransitionError:
            continue
        raise AssertionError(f"{src} -> enriched must be illegal")
    vs.check_transition(vs.ENRICHED, vs.SUPERSEDED)
