"""Retrieval metrics (plan S2.17): recall@k for k in (8, 20, 40) and MRR@40 over mapped gold quotes.

A gold quote is a hit at k when any chunk acceptable for it (`GoldTarget.hits`) is in the top k retrieved ids.
The runner's per-item metric is `recall@8` / `mrr@40` from `eval.graders.retrieval_metrics`; this module adds the
wider cutoffs and the per-category aggregation used by the report and the gate.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence

from eval.mapping import GoldTarget

KS = (8, 20, 40)


def recall_at(targets: Sequence[GoldTarget], retrieved: Sequence[str], k: int) -> float | None:
    mapped = [t for t in targets if t.mapped]
    if not mapped:
        return None
    top = set(retrieved[:k])
    return sum(1 for t in mapped if {h.chunk_id for h in t.hits} & top) / len(mapped)


def mrr_at(targets: Sequence[GoldTarget], retrieved: Sequence[str], k: int = 40) -> float | None:
    mapped = [t for t in targets if t.mapped]
    if not mapped:
        return None
    union = {h.chunk_id for t in mapped for h in t.hits}
    return next((1.0 / (i + 1) for i, c in enumerate(retrieved[:k]) if c in union), 0.0)


def retrieval_report(targets: Sequence[GoldTarget], retrieved: Sequence[str]) -> dict[str, float]:
    out: dict[str, float] = {}
    for k in KS:
        r = recall_at(targets, retrieved, k)
        if r is not None:
            out[f"recall@{k}"] = r
    m = mrr_at(targets, retrieved)
    if m is not None:
        out["mrr@40"] = m
    return out


def by_category(rows: Iterable[tuple[str, dict[str, float]]]) -> dict[str, dict[str, float]]:
    """(category, per-item metrics) -> category -> metric -> mean."""
    acc: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for cat, m in rows:
        for k, v in m.items():
            acc[cat][k].append(v)
    return {c: {k: sum(v) / len(v) for k, v in ms.items()} for c, ms in acc.items()}
