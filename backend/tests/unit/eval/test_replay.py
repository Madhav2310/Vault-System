"""S11.02: offline ranking replay."""

import pytest
from eval.mapping import ChunkHit, GoldTarget
from eval.replay import Cand, aggregate, rank, score_item

import tests.support.evalkit  # noqa: F401  (puts the repo root on sys.path)


def _t(*chunks: str) -> GoldTarget:
    return GoldTarget("i", "d", "q", None, tuple(ChunkHit(c, 0, 1, "exact", 100.0, True) for c in chunks))


CANDS = [
    Cand("a", rrf=0.9, rerank=0.1, vector_rank=3, lexical_rank=None, selected=False),
    Cand("b", rrf=0.8, rerank=0.9, vector_rank=1, lexical_rank=2, selected=True, result_index=1),
    Cand("c", rrf=0.7, rerank=None, vector_rank=None, lexical_rank=1, selected=True, result_index=0),
    Cand("d", rrf=0.1, rerank=0.5, vector_rank=2, lexical_rank=None, selected=False),
]


def test_modes_match_hand_values() -> None:
    assert rank(CANDS, "rrf") == ["a", "b", "c", "d"]
    assert rank(CANDS, "vector") == ["b", "d", "a"]  # candidates without a vector rank are absent
    assert rank(CANDS, "lexical") == ["c", "b"]
    assert rank(CANDS, "rerank") == ["b", "d", "a"]  # unreranked candidates are absent
    assert rank(CANDS, "final") == ["c", "b", "d", "a"]  # selected in evidence order, then rerank, then rrf
    with pytest.raises(ValueError):
        rank(CANDS, "bogus")
    s = score_item("i", "lookup", [_t("b"), _t("zzz")], rank(CANDS, "final"))
    assert s is not None and s.hits[8] == [True, False] and s.mrr == pytest.approx(1 / 2)


def test_quote_vs_item_aggregation() -> None:
    a = score_item("A", "lookup", [_t("a"), _t("zzz")], ["a"])  # 1 of 2 quotes hit
    b = score_item("B", "lookup", [_t("a")], ["a"])  # 1 of 1
    assert a and b
    agg = aggregate([a, b])
    assert agg["quote_recall@8"] == pytest.approx(2 / 3)
    assert agg["item_recall@8"] == pytest.approx((0.5 + 1.0) / 2)
    assert agg["items"] == 2 and agg["quotes"] == 3


def test_unmapped_excluded() -> None:
    assert score_item("i", "lookup", [GoldTarget("i", "d", "q", None, ())], ["a"]) is None
    assert aggregate([]) == {}
