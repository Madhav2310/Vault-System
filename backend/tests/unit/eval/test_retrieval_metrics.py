"""S2.17 AC1: retrieval metrics match hand-computed values on a toy ranking."""

import pytest

from eval.mapping import ChunkHit, GoldTarget
from eval.metrics.retrieval import by_category, mrr_at, recall_at, retrieval_report


def _t(*chunks: str) -> GoldTarget:
    hits = tuple(ChunkHit(c, 0, 1, "exact", 100.0, True) for c in chunks)
    return GoldTarget("i", "d", "q", None, hits)


def test_recall_mrr() -> None:
    ranking = [f"c{i}" for i in range(1, 41)]
    targets = [_t("c3"), _t("c9", "c30"), _t("c25"), _t("zzz"), GoldTarget("i", "d", "q", None, ())]
    # the unmapped target is excluded; 4 mapped: c3@3, c9@9 (c30 too), c25@25, zzz never
    assert recall_at(targets, ranking, 8) == pytest.approx(1 / 4)
    assert recall_at(targets, ranking, 20) == pytest.approx(2 / 4)
    assert recall_at(targets, ranking, 40) == pytest.approx(3 / 4)
    assert mrr_at(targets, ranking) == pytest.approx(1 / 3)  # first acceptable chunk for any quote: c3
    assert mrr_at(targets, ["x"] * 3) == 0.0
    assert recall_at([GoldTarget("i", "d", "q", None, ())], ranking, 8) is None
    assert retrieval_report(targets, ranking) == {
        "recall@8": pytest.approx(0.25),
        "recall@20": pytest.approx(0.5),
        "recall@40": pytest.approx(0.75),
        "mrr@40": pytest.approx(1 / 3),
    }
    assert by_category([("a", {"recall@8": 1.0}), ("a", {"recall@8": 0.0}), ("b", {"recall@8": 1.0})]) == {
        "a": {"recall@8": 0.5},
        "b": {"recall@8": 1.0},
    }
