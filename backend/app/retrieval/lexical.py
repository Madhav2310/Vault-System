"""Lexical search (design 7.2, 7.3): an OR tsquery of the query's English lexemes on `tsv_en`, ranked by
`ts_rank_cd`, plus a `simple` query over the query's identifier-like tokens on `tsv_ids` (`id_match`).

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

from app.retrieval.numbers import number_variants
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


# ts_rank_cd normalisation flag of the "v2" lexical leg: 1 = divide by 1 + log(chunk length). Flag 0 (the v1
# default) scores a 20-word and a 300-word chunk with the same hit identically; flag 2 (divide by length)
# separates them but collapses a long table chunk to ~1/30 of a short one. Flag 1 orders short > long without
# that collapse (fixture: tests/integration/retrieval/test_lexical_v2.py::test_length_normalisation).
RANK_NORM_V2 = 1

MIN_MATCH_FROM = (
    4  # queries with at least this many lexemes must match 2 of them (cuts one-common-word noise)
)


def lexical_statement(
    owner_id: uuid.UUID, version_ids: Sequence[uuid.UUID], query: str, k: int, variant: str = "v1"
) -> tuple[str, dict[str, Any]]:
    """The SQL and parameters of one lexical search (exposed so tests can EXPLAIN the exact statement).

    OR semantics: the query's stemmed, stopword-free lexemes (`to_tsvector('english')`) are joined
    with `|` as quoted literals, so no user text is ever parsed as tsquery syntax. A chunk matches when it
    has at least one lexeme (two when the query has `MIN_MATCH_FROM`+), ranked by `ts_rank_cd`: OR score plus
    2x the AND score, which rewards proximity. Identifier tokens keep their own `simple` match on `tsv_ids`
    and bypass the minimum.

    `variant="v2"` (selected by `lexical: v2` in the retrieval config): `ts_rank_cd` with length
    normalisation (`RANK_NORM_V2`), and every amount in the query also matches its equivalent written forms
    (`app.retrieval.numbers`: "$1.2 billion" also finds "1,200 million"). Each amount counts as one
    matched lexeme for the minimum. Without amounts the v2 statement differs from v1 only by the rank flag."""
    ids = identifier_query(query)
    params: dict[str, Any] = {"o": owner_id, "vs": list(version_ids), "q": query, "k": k}
    v2 = variant == "v2"
    norm = f", {RANK_NORM_V2}" if v2 else ""
    if '"' in query:  # an explicit quoted phrase keeps websearch semantics (phrase / AND / -exclusion)
        return _websearch_statement(ids, params, norm)
    vq_cte, n_groups = "", 0
    if v2:  # one phrase-tsquery OR per amount, each form a bound parameter
        groups = number_variants(query)
        n_groups = len(groups)
        cols = []
        for gi, forms in enumerate(groups):
            for fi, form in enumerate(forms):
                params[f"nv{gi}_{fi}"] = form
            phrases = " || ".join(f"phraseto_tsquery('english', :nv{gi}_{fi})" for fi in range(len(forms)))
            cols.append(f"({phrases}) AS q{gi}")
        if cols:
            vq_cte = f", vq AS (SELECT {', '.join(cols)})"
    ctes = (
        "WITH ql AS (SELECT tsvector_to_array(to_tsvector('english', :q)) AS lex), "  # noqa: S608 (only the MIN_MATCH_FROM constant is interpolated)
        "qq AS (SELECT lex, CASE WHEN cardinality(lex) = 0 THEN NULL ELSE "
        "(SELECT string_agg(quote_literal(l), ' | ') FROM unnest(lex) AS l)::tsquery END AS q, "
        "CASE WHEN cardinality(lex) = 0 THEN NULL ELSE "
        "(SELECT string_agg(quote_literal(l), ' & ') FROM unnest(lex) AS l)::tsquery END AS qa, "
        f"CASE WHEN cardinality(lex) >= {MIN_MATCH_FROM} THEN 2 ELSE 1 END AS need FROM ql){vq_cte} "
    )
    matched = "(SELECT count(*) FROM unnest(qq.lex) AS l WHERE c.tsv_en @@ quote_literal(l)::tsquery)"
    or_q = "qq.q"
    match_q = "(qq.q IS NOT NULL AND c.tsv_en @@ qq.q)"
    if n_groups:  # each amount counts as one matched lexeme for the minimum
        vall = " || ".join(f"vq.q{gi}" for gi in range(n_groups))
        or_q = f"COALESCE(qq.q || {vall}, {vall})"
        any_var = " OR ".join(f"c.tsv_en @@ vq.q{gi}" for gi in range(n_groups))
        match_q = f"({match_q} OR {any_var})"
        matched += "".join(f" + (CASE WHEN c.tsv_en @@ vq.q{gi} THEN 1 ELSE 0 END)" for gi in range(n_groups))
    # cover density of an OR query ignores proximity, so chunks that also satisfy the AND query rank higher
    rank_or = f"COALESCE(ts_rank_cd(c.tsv_en, {or_q}{norm}), 0)"
    score = f"({rank_or} + 2 * COALESCE(ts_rank_cd(c.tsv_en, qq.qa{norm}), 0))"
    match = f"({match_q} AND {matched} >= qq.need)"
    id_match = "false"
    if ids:
        params["ids"] = ids
        idq = "websearch_to_tsquery('simple', :ids)"
        score = f"GREATEST({score}, ts_rank_cd(c.tsv_ids, {idq}{norm}))"
        match = f"({match} OR c.tsv_ids @@ {idq})"
        id_match = f"COALESCE(c.tsv_ids @@ {idq}, false)"
    frm = "chunks c, qq, vq" if n_groups else "chunks c, qq"
    # only constant fragments are interpolated; the query text itself is always a bound parameter
    sql = (
        f"{ctes}SELECT c.id, c.version_id, {score} AS score, {id_match} AS id_match FROM {frm} "  # noqa: S608
        f"WHERE c.owner_id = :o AND c.version_id = ANY(CAST(:vs AS uuid[])) AND {match} "
        "ORDER BY score DESC, c.id LIMIT :k"
    )
    return sql, params


def _websearch_statement(ids: str, params: dict[str, Any], norm: str = "") -> tuple[str, dict[str, Any]]:
    en = "websearch_to_tsquery('english', :q)"
    score = f"ts_rank_cd(c.tsv_en, {en}{norm})"
    match = f"c.tsv_en @@ {en}"
    id_match = "false"
    if ids:
        params["ids"] = ids
        idq = "websearch_to_tsquery('simple', :ids)"
        score = f"GREATEST({score}, ts_rank_cd(c.tsv_ids, {idq}{norm}))"
        match = f"({match} OR c.tsv_ids @@ {idq})"
        id_match = f"COALESCE(c.tsv_ids @@ {idq}, false)"
    sql = (
        f"SELECT c.id, c.version_id, {score} AS score, {id_match} AS id_match FROM chunks c "  # noqa: S608
        f"WHERE c.owner_id = :o AND c.version_id = ANY(CAST(:vs AS uuid[])) AND {match} "
        "ORDER BY score DESC, c.id LIMIT :k"
    )
    return sql, params


async def lexical_search(
    session: AsyncSession, owner_id: uuid.UUID, scope: Scope, query: str, k: int = 40, variant: str = "v1"
) -> list[LexicalHit]:
    """Top-k chunks in `scope` for `query`: score descending, chunk id ascending on ties."""
    query = query.replace("\x00", " ").strip()
    if not query or k <= 0 or not scope.version_ids:
        return []
    sql, params = lexical_statement(owner_id, scope.version_ids, query, k, variant)
    rows = (await session.execute(text(sql), params)).all()
    return [LexicalHit(chunk_id=r[0], version_id=r[1], score=float(r[2]), id_match=bool(r[3])) for r in rows]
