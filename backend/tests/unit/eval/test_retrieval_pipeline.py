"""The retrieval-only pipeline: config resolution and the candidate rankings it can score."""

import pytest
from eval.pipeline_retrieval import Cand, rank, resolve_config

CANDS = [
    Cand("a", rrf=0.9, rerank=0.1, vector_rank=3, lexical_rank=None, selected=False),
    Cand("b", rrf=0.8, rerank=0.9, vector_rank=1, lexical_rank=2, selected=True, result_index=1),
    Cand("c", rrf=0.7, rerank=None, vector_rank=None, lexical_rank=1, selected=True, result_index=0),
    Cand("d", rrf=0.1, rerank=0.5, vector_rank=2, lexical_rank=None, selected=False),
]


def test_unknown_config() -> None:
    with pytest.raises(SystemExit) as e:
        resolve_config("retrieval@999")
    assert "retrieval@999.yaml" in str(e.value)
    assert resolve_config("retrieval@2").version == "retrieval@2"


def test_ranking_modes_match_hand_values() -> None:
    assert rank(CANDS, "rrf") == ["a", "b", "c", "d"]
    assert rank(CANDS, "vector") == ["b", "d", "a"]  # candidates without a vector rank are absent
    assert rank(CANDS, "lexical") == ["c", "b"]
    assert rank(CANDS, "rerank") == ["b", "d", "a"]  # unreranked candidates are absent
    assert rank(CANDS, "final") == ["c", "b", "d", "a"]  # selected in evidence order, then rerank, then rrf
    with pytest.raises(ValueError):
        rank(CANDS, "bogus")
