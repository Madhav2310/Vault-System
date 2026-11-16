"""Exact scoped vector search: brute-force cosine distance over one owner's chunks in the
allowed versions, on `halfvec(1536)`. No ANN index is used here (HNSW is the escape hatch at larger scale).

Every statement is filtered by `owner_id` (partition pruning) and the allowed version ids; the query vector is
always a bound parameter. Order is distance ascending, then chunk id, so results are deterministic.
An optional per-document quota keeps at most N hits per document before fusion.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.retrieval.scope import Scope


@dataclass(frozen=True)
class VectorHit:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    version_id: uuid.UUID
    distance: float  # cosine distance, 0 = identical


def vector_literal(vector: Sequence[float]) -> str:
    return "[" + ",".join(repr(float(x)) for x in vector) + "]"


def vector_statement(
    owner_id: uuid.UUID,
    version_ids: Sequence[uuid.UUID],
    query_vector: Sequence[float],
    k: int,
    per_doc_quota: int | None = None,
) -> tuple[str, dict[str, Any]]:
    """The SQL and parameters of one exact search (exposed so tests can EXPLAIN the exact statement)."""
    params: dict[str, Any] = {
        "o": owner_id,
        "vs": list(version_ids),
        "q": vector_literal(query_vector),
        "k": k,
    }
    dist = "c.embedding <=> CAST(:q AS halfvec)"
    where = "c.owner_id = :o AND c.version_id = ANY(CAST(:vs AS uuid[])) AND c.embedding IS NOT NULL"
    if per_doc_quota is None:
        sql = (
            f"SELECT c.id, c.document_id, c.version_id, {dist} AS distance FROM chunks c "  # noqa: S608
            f"WHERE {where} ORDER BY distance, c.id LIMIT :k"
        )
    else:
        params["quota"] = per_doc_quota
        sql = (
            "SELECT id, document_id, version_id, distance FROM ("  # noqa: S608
            f"SELECT c.id, c.document_id, c.version_id, {dist} AS distance, "
            f"row_number() OVER (PARTITION BY c.document_id ORDER BY {dist}, c.id) AS rn "
            f"FROM chunks c WHERE {where}) ranked "
            "WHERE rn <= :quota ORDER BY distance, id LIMIT :k"
        )
    return sql, params


async def vector_search(
    session: AsyncSession,
    owner_id: uuid.UUID,
    scope: Scope,
    query_vector: Sequence[float],
    k: int = 40,
    per_doc_quota: int | None = None,
) -> list[VectorHit]:
    """Top-k embedded chunks in `scope` by cosine distance (at most `per_doc_quota` per document)."""
    if k <= 0 or not scope.version_ids or (per_doc_quota is not None and per_doc_quota <= 0):
        return []
    sql, params = vector_statement(owner_id, scope.version_ids, query_vector, k, per_doc_quota)
    rows = (await session.execute(text(sql), params)).all()
    return [VectorHit(chunk_id=r[0], document_id=r[1], version_id=r[2], distance=float(r[3])) for r in rows]
