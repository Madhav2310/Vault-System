"""Lexical search (design 7.2, 7.3): `websearch_to_tsquery('english')` on `tsv_en` ranked by `ts_rank_cd`,
plus a `simple` query over the identifier-like tokens of the query on `tsv_ids` (`id_match=true`).

Every statement is filtered by `owner_id` (partition pruning) and the allowed version ids. No LLM, no
document text in logs.
"""

import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.retrieval.scope import Scope

# A token with at least one digit: clause numbers (4.2, 4.2(b)), ids (INV-2024-001), amounts, dates.
_ID_TOKEN = re.compile(r"[A-Za-z0-9$]*\d[\w.,/\-$]*(?:\([A-Za-z0-9]{1,3}\))*")


@dataclass(frozen=True)
class LexicalHit:
    chunk_id: uuid.UUID
    version_id: uuid.UUID
    score: float
    id_match: bool = False


def identifier_query(query: str) -> str:
    """Identifier-like tokens of `query`, space separated (empty when there are none)."""
    return " ".join(t.rstrip(".,;:") for t in _ID_TOKEN.findall(query) if t.rstrip(".,;:"))


def lexical_statement(
    owner_id: uuid.UUID, version_ids: Sequence[uuid.UUID], query: str, k: int
) -> tuple[str, dict[str, Any]]:
    """The SQL and parameters of one lexical search (exposed so tests can EXPLAIN the exact statement)."""
    ids = identifier_query(query)
    params: dict[str, Any] = {"o": owner_id, "vs": list(version_ids), "q": query, "k": k}
    en = "websearch_to_tsquery('english', :q)"
    score = f"ts_rank_cd(c.tsv_en, {en})"
    match = f"c.tsv_en @@ {en}"
    id_match = "false"
    if ids:
        params["ids"] = ids
        idq = "websearch_to_tsquery('simple', :ids)"
        score = f"GREATEST({score}, ts_rank_cd(c.tsv_ids, {idq}))"
        match = f"({match} OR c.tsv_ids @@ {idq})"
        id_match = f"COALESCE(c.tsv_ids @@ {idq}, false)"
    # only constant fragments are interpolated; the query text itself is always a bound parameter
    sql = (
        f"SELECT c.id, c.version_id, {score} AS score, {id_match} AS id_match FROM chunks c "  # noqa: S608
        f"WHERE c.owner_id = :o AND c.version_id = ANY(CAST(:vs AS uuid[])) AND {match} "
        "ORDER BY score DESC, c.id LIMIT :k"
    )
    return sql, params


async def lexical_search(
    session: AsyncSession, owner_id: uuid.UUID, scope: Scope, query: str, k: int = 40
) -> list[LexicalHit]:
    """Top-k chunks in `scope` for `query`: score descending, chunk id ascending on ties."""
    query = query.replace("\x00", " ").strip()
    if not query or k <= 0 or not scope.version_ids:
        return []
    sql, params = lexical_statement(owner_id, scope.version_ids, query, k)
    rows = (await session.execute(text(sql), params)).all()
    return [LexicalHit(chunk_id=r[0], version_id=r[1], score=float(r[2]), id_match=bool(r[3])) for r in rows]
