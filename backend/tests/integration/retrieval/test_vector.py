"""Exact scoped vector search: top-k, brute-force equality, per-doc quota, scope isolation, plan."""

import json
import random
import time
from collections.abc import Callable
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.retrieval.scope import Scope, compute_scope
from app.retrieval.vector import vector_search, vector_statement
from tests.support.docs import seed_document
from tests.support.vectors import cosine_distance, seed_vectors, to_half, unit_vector

SM = async_sessionmaker[AsyncSession]


def _reference(q: list[float], rows: dict[Any, list[float]], k: int) -> list[Any]:
    """Brute-force cosine ranking over the stored (float16-rounded) vectors, id as tie-break."""
    qh = to_half(q)
    ranked = sorted((cosine_distance(qh, to_half(v)), cid) for cid, v in rows.items())
    return [cid for _, cid in ranked[:k]]


async def test_returns_k(sm: SM, make_owner: Callable[[], Any]) -> None:
    o = await make_owner()
    doc = await seed_document(sm, o.id)
    rng = random.Random(1)
    await seed_vectors(sm, o.id, doc, [unit_vector(rng) for _ in range(60)] + [None] * 5)  # 5 not embedded
    q = unit_vector(rng)
    async with sm() as s:
        scope = await compute_scope(s, o.id, None)
        assert len(await vector_search(s, o.id, scope, q, k=40)) == 40
        assert len(await vector_search(s, o.id, scope, q, k=100)) == 60  # min(k, embedded scoped rows)
        assert await vector_search(s, o.id, Scope.empty, q, k=40) == []
        assert await vector_search(s, o.id, scope, q, k=0) == []


async def test_matches_bruteforce(sm: SM, make_owner: Callable[[], Any]) -> None:
    o = await make_owner()
    doc = await seed_document(sm, o.id)
    rng = random.Random(2)
    vecs = [unit_vector(rng) for _ in range(300)]
    ids = await seed_vectors(sm, o.id, doc, vecs)
    q = unit_vector(rng)
    async with sm() as s:
        scope = await compute_scope(s, o.id, None)
        hits = await vector_search(s, o.id, scope, q, k=40)
    assert [h.chunk_id for h in hits] == _reference(q, dict(zip(ids, vecs, strict=True)), 40)
    assert [h.distance for h in hits] == sorted(h.distance for h in hits)
    expect = cosine_distance(to_half(q), to_half(vecs[ids.index(hits[0].chunk_id)]))
    assert abs(hits[0].distance - expect) < 1e-3
    assert all(h.version_id == doc.version_id and h.document_id == doc.document_id for h in hits)


async def test_deterministic_tie_break(sm: SM, make_owner: Callable[[], Any]) -> None:
    o = await make_owner()
    doc = await seed_document(sm, o.id)
    v = unit_vector(random.Random(3))
    ids = await seed_vectors(sm, o.id, doc, [v] * 6)  # identical vectors: only the id can order them
    async with sm() as s:
        scope = await compute_scope(s, o.id, None)
        hits = await vector_search(s, o.id, scope, v, k=6)
    assert [h.chunk_id for h in hits] == sorted(ids)


async def test_per_doc_quota(sm: SM, make_owner: Callable[[], Any]) -> None:
    o = await make_owner()
    rng = random.Random(4)
    docs = [await seed_document(sm, o.id, title=f"d{i}") for i in range(3)]
    stored: dict[Any, list[float]] = {}
    owner_of: dict[Any, Any] = {}
    for d in docs:
        vecs = [unit_vector(rng) for _ in range(30)]
        for cid, v in zip(await seed_vectors(sm, o.id, d, vecs), vecs, strict=True):
            stored[cid], owner_of[cid] = v, d.document_id
    q = unit_vector(rng)
    async with sm() as s:
        scope = await compute_scope(s, o.id, None)
        hits = await vector_search(s, o.id, scope, q, k=40, per_doc_quota=5)
        free = await vector_search(s, o.id, scope, q, k=40)
    counts = {d.document_id: sum(h.document_id == d.document_id for h in hits) for d in docs}
    assert len(hits) == 15 and all(c == 5 for c in counts.values()), counts
    expected: list[Any] = []
    for d in docs:
        expected += _reference(q, {c: v for c, v in stored.items() if owner_of[c] == d.document_id}, 5)
    expected.sort(key=lambda c: (cosine_distance(to_half(q), to_half(stored[c])), c))
    assert [h.chunk_id for h in hits] == expected
    assert len(free) == 40  # without a quota the best 40 may come from anywhere


async def test_never_outside_scope(sm: SM, make_owner: Callable[[], Any]) -> None:
    o, other = await make_owner(), await make_owner()
    rng = random.Random(5)
    docs = [await seed_document(sm, o.id, title=f"d{i}") for i in range(4)]
    odoc = await seed_document(sm, other.id)
    shared = [unit_vector(rng) for _ in range(20)]
    mine: dict[Any, set[Any]] = {}
    for d in docs:
        mine[d.version_id] = set(await seed_vectors(sm, o.id, d, shared))
    theirs = set(await seed_vectors(sm, other.id, odoc, shared))  # identical vectors, other owner
    q = shared[0]
    async with sm() as s:
        full = await compute_scope(s, o.id, None)
        for _ in range(25):
            pick = rng.sample(docs, rng.randint(1, 4))
            scope = Scope(entries=tuple(sorted((d.document_id, d.version_id) for d in pick)))
            hits = await vector_search(s, o.id, scope, q, k=40)
            allowed = set().union(*(mine[d.version_id] for d in pick))
            got = {h.chunk_id for h in hits}
            assert got <= allowed and not (got & theirs) and len(got) == min(40, len(allowed))
        # a scope entry the owner does not own matches nothing even though its rows exist
        foreign = Scope(entries=((odoc.document_id, odoc.version_id),))
        assert await vector_search(s, o.id, foreign, q, k=40) == []
        assert len(await vector_search(s, o.id, full, q, k=100)) == 80


async def _explain(s: AsyncSession, sql: str, params: dict[str, Any]) -> dict[str, Any]:
    raw = (await s.execute(text("EXPLAIN (ANALYZE, FORMAT JSON) " + sql), params)).scalar_one()
    plan: dict[str, Any] = (raw if isinstance(raw, list) else json.loads(raw))[0]["Plan"]
    return plan


def _nodes(plan: dict[str, Any]) -> list[dict[str, Any]]:
    out = [plan]
    for child in plan.get("Plans", []):
        out += _nodes(child)
    return out


async def test_partition_pruning(sm: SM, make_owner: Callable[[], Any], capsys: Any) -> None:
    o, other = await make_owner(), await make_owner()
    rng = random.Random(6)
    doc, odoc = await seed_document(sm, o.id), await seed_document(sm, other.id)
    await seed_vectors(sm, o.id, doc, [unit_vector(rng) for _ in range(1500)])
    await seed_vectors(sm, other.id, odoc, [unit_vector(rng) for _ in range(1500)])
    q = unit_vector(rng)
    async with sm() as s:
        await s.execute(text("ANALYZE chunks"))
        await s.commit()
        scope = await compute_scope(s, o.id, None)
        t0 = time.perf_counter()
        assert len(await vector_search(s, o.id, scope, q, k=40)) == 40
        wall_ms = (time.perf_counter() - t0) * 1000
        report = []
        for quota in (None, 5):
            sql, params = vector_statement(o.id, scope.version_ids, q, 40, quota)
            plan = await _explain(s, sql, params)
            nodes = _nodes(plan)
            scanned = {n["Relation Name"] for n in nodes if n.get("Relation Name", "").startswith("chunks_p")}
            assert len(scanned) == 1, scanned  # only the owner's hash partition is touched
            report.append(
                f"quota={quota} partitions={sorted(scanned)} nodes={[n['Node Type'] for n in nodes]} "
                f"cost={plan['Total Cost']:.0f} exec_ms={plan.get('Actual Total Time')}"
            )
    with capsys.disabled():
        print(f"\nvector search wall {wall_ms:.1f} ms, 1500 scoped / 3000 rows\n" + "\n".join(report))
