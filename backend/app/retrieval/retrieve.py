"""`retrieve()` facade (design §5.1, §7.3, §7.4): scope + query -> ranked candidates with full provenance.

Pipeline: embed (degrades to lexical-only) -> exact vector search + lexical search -> RRF fusion -> rerank the
top `rerank.candidates` (degrades to fusion order) -> keep `top_n` -> neighbor expansion. Every candidate
carries all the fields of `retrieval_candidates`. A result computed with a degradation is never cached; the
cache key covers the owner, the normalized query, the sorted scope versions and the config version.
Redis trouble means a miss, never an error.
"""

import hashlib
import json
import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass

import redis.asyncio as aioredis
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.retrieval.config import RetrievalConfig, get_config
from app.retrieval.expand import expand_neighbors
from app.retrieval.fusion import FusedCandidate, Ranked, fuse
from app.retrieval.lexical import lexical_search
from app.retrieval.query_embed import QueryEmbedder, normalize_query
from app.retrieval.rerank import RerankClient, RerankPassage
from app.retrieval.scope import Scope
from app.retrieval.vector import vector_search

_log = get_logger("vault.retrieval")
CACHE_PREFIX = "vault:retr:"


@dataclass(frozen=True)
class Candidate:
    """One retrieval candidate: all provenance fields of `retrieval_candidates`."""

    chunk_id: uuid.UUID
    version_id: uuid.UUID
    vector_rank: int | None = None
    vector_distance: float | None = None  # raw cosine distance
    lexical_rank: int | None = None
    lexical_score: float | None = None
    id_match: bool = False
    rrf_score: float | None = None
    rerank_score: float | None = None
    selected: bool = False  # part of the final evidence (reranked top_n or an expansion)
    expanded_from: uuid.UUID | None = None
    result_index: int | None = None  # position in the final evidence order


@dataclass(frozen=True)
class RetrievalResult:
    candidates: tuple[Candidate, ...]
    selected: tuple[uuid.UUID, ...]  # final evidence chunk ids, in evidence order
    degraded: tuple[str, ...]
    config_version: str
    cache_hit: bool = False


def result_cache_key(
    owner_id: uuid.UUID, query: str, version_ids: Sequence[uuid.UUID], config_version: str
) -> str:
    payload = json.dumps(
        [str(owner_id), normalize_query(query), sorted(str(v) for v in set(version_ids)), config_version]
    )
    return CACHE_PREFIX + hashlib.sha256(payload.encode()).hexdigest()


def _encode(r: RetrievalResult) -> str:
    return json.dumps(asdict(r), default=str)


def _decode(raw: str) -> RetrievalResult:
    d = json.loads(raw)
    cands = tuple(
        Candidate(
            **{
                **c,
                "chunk_id": uuid.UUID(c["chunk_id"]),
                "version_id": uuid.UUID(c["version_id"]),
                "expanded_from": uuid.UUID(c["expanded_from"]) if c["expanded_from"] else None,
            }
        )
        for c in d["candidates"]
    )
    return RetrievalResult(
        cands,
        tuple(uuid.UUID(x) for x in d["selected"]),
        tuple(d["degraded"]),
        d["config_version"],
        cache_hit=True,
    )


class Retriever:
    def __init__(
        self,
        embedder: QueryEmbedder,
        reranker: RerankClient,
        redis: aioredis.Redis | None,
        cfg: RetrievalConfig | None = None,
    ) -> None:
        self._embedder = embedder
        self._reranker = reranker
        self._redis = redis
        self._cfg = cfg or get_config()

    @property
    def config(self) -> RetrievalConfig:
        return self._cfg

    async def retrieve(
        self, session: AsyncSession, owner_id: uuid.UUID, scope: Scope, query: str
    ) -> RetrievalResult:
        cfg = self._cfg
        version_ids = [v for _, v in scope.entries]
        if not version_ids or not query.strip():
            return RetrievalResult((), (), (), cfg.version)
        key = result_cache_key(owner_id, query, version_ids, cfg.version)
        cached = await self._cache_get(key)
        if cached is not None:
            return cached

        degraded: list[str] = []
        emb = await self._embedder.embed(query, owner_id=owner_id)
        vec_ranked: list[Ranked] = []
        distance: dict[uuid.UUID, float] = {}
        if emb.vector is not None:
            hits = await vector_search(
                session, owner_id, scope, emb.vector, k=cfg.k_vec, per_doc_quota=cfg.per_doc_quota
            )
            distance = {h.chunk_id: h.distance for h in hits}
            vec_ranked = [Ranked(h.chunk_id, h.version_id, 1.0 - h.distance) for h in hits]
        elif emb.degraded:
            degraded.append(emb.degraded)
        lex = await lexical_search(session, owner_id, scope, query, k=cfg.k_lex)
        lex_ranked = [Ranked(h.chunk_id, h.version_id, h.score, h.id_match) for h in lex]

        fused = fuse(vec_ranked, lex_ranked, k=cfg.rrf_k)
        pool = fused[: cfg.rerank.candidates]
        texts = await self._passages(session, owner_id, [c.chunk_id for c in pool])
        pool = [c for c in pool if c.chunk_id in texts]  # a chunk deleted mid-request drops out
        await session.rollback()  # end the read transaction: no connection held across the reranker call
        rr = await self._reranker.rerank(
            query, [RerankPassage(c.chunk_id, texts[c.chunk_id]) for c in pool], cfg.rerank.top_n
        )
        if rr.degraded and rr.degraded_reason:
            degraded.append(rr.degraded_reason)
        scores = {c.chunk_id: rr.scores[i] for i, c in enumerate(pool)} if rr.scores else {}
        top_ids = [it.chunk_id for it in rr.items]

        final: list[tuple[uuid.UUID, uuid.UUID | None]] = [(cid, None) for cid in top_ids]
        if cfg.expansion.enabled and top_ids:
            exp = await expand_neighbors(session, owner_id, top_ids, cfg.expansion.max_evidence_tokens)
            final = [(e.chunk_id, e.expanded_from) for e in exp]
        order = {cid: (i, ef) for i, (cid, ef) in enumerate(final)}

        by_id: dict[uuid.UUID, FusedCandidate] = {c.chunk_id: c for c in fused}
        cands: list[Candidate] = []
        for c in fused:
            pos = order.get(c.chunk_id)
            cands.append(_candidate(c, distance, scores.get(c.chunk_id), pos))
        seen = set(by_id)
        for cid, ef in final:  # expansion neighbors that were never retrieved
            if cid not in seen:
                vid = await self._version_of(session, owner_id, cid)
                if vid is not None:
                    cands.append(
                        Candidate(cid, vid, selected=True, expanded_from=ef, result_index=order[cid][0])
                    )
        result = RetrievalResult(tuple(cands), tuple(cid for cid, _ in final), tuple(degraded), cfg.version)
        if not degraded and emb.vector is not None and not rr.degraded:
            await self._cache_set(key, result)
        return result

    async def _passages(
        self, session: AsyncSession, owner_id: uuid.UUID, ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, str]:
        if not ids:
            return {}
        rows = await session.execute(
            text("SELECT id, embedding_text FROM chunks WHERE owner_id = :o AND id = ANY(:ids)"),
            {"o": owner_id, "ids": list(ids)},
        )
        return {r[0]: r[1] for r in rows}

    @staticmethod
    async def _version_of(
        session: AsyncSession, owner_id: uuid.UUID, chunk_id: uuid.UUID
    ) -> uuid.UUID | None:
        row = (
            await session.execute(
                text("SELECT version_id FROM chunks WHERE owner_id = :o AND id = :c"),
                {"o": owner_id, "c": chunk_id},
            )
        ).first()
        return row[0] if row else None

    async def _cache_get(self, key: str) -> RetrievalResult | None:
        if self._redis is None or self._cfg.cache_ttl_s == 0:
            return None
        try:
            raw = await self._redis.get(key)
            return _decode(raw.decode() if isinstance(raw, bytes) else raw) if raw else None
        except (RedisError, OSError, ValueError, KeyError, TypeError):
            _log.warning("retrieval cache unavailable (get)")
            return None

    async def _cache_set(self, key: str, result: RetrievalResult) -> None:
        if self._redis is None or self._cfg.cache_ttl_s == 0:
            return
        try:
            await self._redis.set(key, _encode(result), ex=self._cfg.cache_ttl_s)
        except (RedisError, OSError):
            _log.warning("retrieval cache unavailable (set)")


def _candidate(
    c: FusedCandidate,
    distance: dict[uuid.UUID, float],
    rerank_score: float | None,
    pos: tuple[int, uuid.UUID | None] | None,
) -> Candidate:
    return Candidate(
        chunk_id=c.chunk_id,
        version_id=c.version_id,
        vector_rank=c.vector_rank,
        vector_distance=distance.get(c.chunk_id),
        lexical_rank=c.lexical_rank,
        lexical_score=c.lexical_score,
        id_match=c.id_match,
        rrf_score=c.score,
        rerank_score=rerank_score,
        selected=pos is not None,
        expanded_from=pos[1] if pos else None,
        result_index=pos[0] if pos else None,
    )
