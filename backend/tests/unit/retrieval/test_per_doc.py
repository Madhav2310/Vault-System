import uuid

from app.retrieval.config import get_config
from app.retrieval.fusion import FusedCandidate
from app.retrieval.per_doc import add_per_document


def _c(rank: int, version: uuid.UUID) -> FusedCandidate:
    return FusedCandidate(uuid.UUID(int=rank), version, rank, 1.0 / rank, rank, None, None, None, False)


A, B, C = (uuid.UUID(int=1000 + i) for i in range(3))
# ranks 1-8 are document A, 9-12 document B, 13-14 document C
FUSED = [_c(r, A) for r in range(1, 9)] + [_c(r, B) for r in range(9, 13)] + [_c(r, C) for r in (13, 14)]


def ids(*ranks: int) -> list[uuid.UUID]:
    return [uuid.UUID(int=r) for r in ranks]


def test_single_document_scope_is_unchanged() -> None:
    assert add_per_document(ids(1, 2, 3), FUSED, 6, 1) == ids(1, 2, 3)


def test_switched_off_is_unchanged() -> None:
    assert add_per_document(ids(1, 2, 3), FUSED, 0, 3) == ids(1, 2, 3)


def test_each_document_gets_its_own_best_chunks_after_the_global_top() -> None:
    out = add_per_document(ids(1, 2, 3), FUSED, 2, 3)
    assert out[:3] == ids(1, 2, 3)  # global order kept
    assert out[3:] == ids(9, 10, 13, 14)  # A already has 3; B and C add their best 2 in rank order


def test_chunks_already_selected_count_toward_the_per_document_limit() -> None:
    out = add_per_document(ids(1, 2, 9), FUSED, 2, 3)
    assert out == ids(1, 2, 9, 10, 13, 14)


def test_no_duplicates_and_deterministic() -> None:
    out = add_per_document(ids(1, 9, 13), FUSED, 6, 3)
    assert len(out) == len(set(out)) == 12  # A 6, B 4 (all it has), C 2
    assert out == add_per_document(ids(1, 9, 13), FUSED, 6, 3)


def test_retrieval_3_config_loads() -> None:
    cfg = get_config("retrieval@3")
    assert (cfg.rerank.top_n, cfg.per_doc_evidence, cfg.expansion.max_evidence_tokens) == (12, 6, 8000)
    assert get_config("retrieval@2").per_doc_evidence == 0
