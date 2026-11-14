"""Offline ranking replay: re-score stored `retrieval_candidates` against gold.

    cd backend && PYTHONPATH=.. uv run --frozen python -m eval.replay --run latest --rank rrf
    ... --run <eval_run_id> --rank final --by-category
    ... --spend 2026-11-13T00:00:00+00:00          # summed llm_calls.cost_usd since a timestamp (all users)

No model calls and no writes: the session is a read-only transaction. A run's items are the `rag_runs` of the
eval owner(s) created inside the eval run's time window (`latest`: the newest run of each question).
Rankings: rrf (fusion score), vector (vector rank), lexical (lexical rank), rerank (rerank score; unreranked
candidates are absent), final (selected in evidence order, then the rest by rerank then rrf).
Per-quote recall pools every mapped gold quote; per-item recall averages quotes within an item first.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import datetime as dt
import sys
import uuid
from collections import defaultdict
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from eval.mapping import GoldTarget

KS = (8, 20, 40)
RANKS = ("rrf", "vector", "lexical", "rerank", "final")


@dataclass(frozen=True)
class Cand:
    chunk_id: str
    rrf: float | None = None
    rerank: float | None = None
    vector_rank: int | None = None
    lexical_rank: int | None = None
    selected: bool = False
    result_index: int | None = None


def _final_key(c: Cand) -> tuple[int, float, float]:
    return (
        (0, float(c.result_index or 0), 0.0)
        if c.selected
        else (1, -(c.rerank if c.rerank is not None else float("-inf")), -(c.rrf or 0.0))
    )


def rank(cands: Sequence[Cand], mode: str) -> list[str]:
    if mode == "rrf":
        return [c.chunk_id for c in sorted(cands, key=lambda c: -(c.rrf or 0.0))]
    if mode == "vector":
        return [
            c.chunk_id for c in sorted((c for c in cands if c.vector_rank), key=lambda c: c.vector_rank or 0)
        ]
    if mode == "lexical":
        return [
            c.chunk_id
            for c in sorted((c for c in cands if c.lexical_rank), key=lambda c: c.lexical_rank or 0)
        ]
    if mode == "rerank":
        return [
            c.chunk_id
            for c in sorted((c for c in cands if c.rerank is not None), key=lambda c: -(c.rerank or 0.0))
        ]
    if mode == "final":
        return [c.chunk_id for c in sorted(cands, key=_final_key)]
    raise ValueError(f"unknown ranking {mode!r}")


@dataclass
class ItemScore:
    item_id: str
    category: str
    hits: dict[int, list[bool]]  # k -> one bool per mapped quote
    mrr: float


def score_item(
    item_id: str, category: str, targets: Sequence[GoldTarget], ranked: Sequence[str]
) -> ItemScore | None:
    mapped = [t for t in targets if t.mapped]
    if not mapped:
        return None
    ids = [{h.chunk_id for h in t.hits} for t in mapped]
    hits = {k: [bool(s & set(ranked[:k])) for s in ids] for k in KS}
    union = set().union(*ids)
    mrr = next((1.0 / (i + 1) for i, c in enumerate(ranked[:40]) if c in union), 0.0)
    return ItemScore(item_id, category, hits, mrr)


def aggregate(scores: Sequence[ItemScore]) -> dict[str, float]:
    out: dict[str, float] = {}
    if not scores:
        return out
    for k in KS:
        quotes = [h for s in scores for h in s.hits[k]]
        out[f"quote_recall@{k}"] = sum(quotes) / len(quotes)
        out[f"item_recall@{k}"] = sum(sum(s.hits[k]) / len(s.hits[k]) for s in scores) / len(scores)
    out["mrr@40"] = sum(s.mrr for s in scores) / len(scores)
    out["items"] = float(len(scores))
    out["quotes"] = float(sum(len(s.hits[8]) for s in scores))
    return out


def by_category(scores: Sequence[ItemScore]) -> dict[str, dict[str, float]]:
    groups: dict[str, list[ItemScore]] = defaultdict(list)
    for s in scores:
        groups[s.category].append(s)
    return {c: aggregate(v) for c, v in sorted(groups.items())}


@contextlib.asynccontextmanager
async def open_read_only(sm: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    """A session whose transaction is read-only: any write raises."""
    async with sm() as s:
        await s.execute(text("SET TRANSACTION READ ONLY"))
        try:
            yield s
        finally:
            await s.rollback()


async def spend_since(s: AsyncSession, since: dt.datetime) -> float:
    return float(
        (
            await s.execute(
                text("SELECT COALESCE(sum(cost_usd), 0) FROM llm_calls WHERE created_at >= :t"), {"t": since}
            )
        ).scalar_one()
    )


async def _runs(s: AsyncSession, run: str) -> list[tuple[uuid.UUID, uuid.UUID, str]]:
    """(rag_run id, owner id, question) for the eval run (or the newest run per question for `latest`)."""
    if run == "latest":
        rows = await s.execute(
            text(
                "SELECT DISTINCT ON (r.owner_id, r.original_query) r.id, r.owner_id, r.original_query "
                "FROM rag_runs r "
                "JOIN users u ON u.id = r.owner_id WHERE u.email LIKE 'eval-%' "
                "ORDER BY r.owner_id, r.original_query, r.created_at DESC"
            )
        )
    else:
        rows = await s.execute(
            text(
                "SELECT r.id, r.owner_id, r.original_query FROM rag_runs r JOIN users u ON u.id = r.owner_id "
                "JOIN eval_runs e ON e.id = :e WHERE u.email LIKE 'eval-%' AND r.created_at >= e.started_at "
                "AND r.created_at <= COALESCE(e.finished_at, now())"
            ),
            {"e": uuid.UUID(run)},
        )
    return [(r[0], r[1], r[2]) for r in rows]


async def _cands(s: AsyncSession, run_id: uuid.UUID) -> list[Cand]:
    rows = await s.execute(
        text(
            "SELECT chunk_id::text, rrf_score, rerank_score, vector_rank, lexical_rank, selected, "
            "result_index "
            "FROM retrieval_candidates WHERE run_id = :r"
        ),
        {"r": run_id},
    )
    return [Cand(r[0], r[1], r[2], r[3], r[4], r[5], r[6]) for r in rows]


async def replay(sm: async_sessionmaker[AsyncSession], run: str, mode: str) -> tuple[list[ItemScore], int]:
    from eval.loader import load_dataset
    from eval.mapping import map_item
    from eval.pipeline_retrieval import ROOT, _chunks, find_ingested

    ds = load_dataset()
    files = {k: ROOT / m["file"] for k, m in ds.documents.items()}
    scores: list[ItemScore] = []
    skipped = 0
    async with open_read_only(sm) as s:
        runs = await _runs(s, run)
        owners = {o for _, o, _ in runs}
        chunks_by_owner = {}
        for o in owners:
            ids = await find_ingested(sm, o, files)
            chunks_by_owner[o] = await _chunks(sm, o, ids)
        q2item = {i.question: i for i in ds.items if i.question}
        for rid, owner, question in runs:
            item = q2item.get(question)
            if item is None:
                continue
            sc = score_item(
                item.id,
                item.category,
                map_item(item, chunks_by_owner[owner]),
                rank(await _cands(s, rid), mode),
            )
            if sc is None:
                skipped += 1
            else:
                scores.append(sc)
    return scores, skipped


def _fmt(d: dict[str, float]) -> str:
    return "  ".join(
        f"{k}={v:.3f}" if not k.endswith("s") or k == "mrr@40" else f"{k}={int(v)}" for k, v in d.items()
    )


async def _main(a: argparse.Namespace) -> int:
    from app.db.session import get_sessionmaker

    sm = get_sessionmaker()
    if a.spend:
        async with sm() as s:
            print(f"spend_usd {await spend_since(s, dt.datetime.fromisoformat(a.spend)):.4f}")
        return 0
    scores, skipped = await replay(sm, a.run, a.rank)
    print(f"rank={a.rank} run={a.run}")
    print(_fmt(aggregate(scores)))
    print(f"skipped {skipped} items without mapped gold")
    if a.by_category:
        for c, d in by_category(scores).items():
            print(f"  {c:14s} {_fmt(d)}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m eval.replay")
    p.add_argument("--run", default="latest", help="eval run id or `latest`")
    p.add_argument("--rank", choices=RANKS, default="final")
    p.add_argument("--by-category", action="store_true")
    p.add_argument(
        "--spend", metavar="SINCE_ISO", help="print summed llm_calls cost since this timestamp and exit"
    )
    return asyncio.run(_main(p.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
