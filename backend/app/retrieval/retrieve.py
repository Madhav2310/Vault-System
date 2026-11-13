"""`retrieve()` facade (design §5.1, §7.3, §7.4): scope + query -> ranked candidates with full provenance.

Pipeline: embed (degrades to lexical-only) -> exact vector search + lexical search -> RRF fusion -> rerank the
top `rerank.candidates` (degrades to fusion order) -> keep `top_n` -> neighbor expansion. Every candidate
carries all the fields of `retrieval_candidates`. A result computed with a degradation is never cached; the
cache key covers the owner, the normalized query, the sorted scope versions and the config version.
Redis trouble means a miss, never an error.
"""

import asyncio
import dataclasses
import hashlib
import json
import time
import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field

import redis.asyncio as aioredis
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import get_logger
from app.retrieval.config import RetrievalConfig, get_config
from app.retrieval.expand import expand_neighbors
from app.retrieval.fusion import FusedCandidate, Ranked, fuse
from app.retrieval.lexical import LexicalHit, lexical_search
from app.retrieval.query_embed import QueryEmbedder, QueryEmbedding, normalize_query
from app.retrieval.rerank import RerankClient, RerankedItem, RerankPassage, RerankResult
from app.retrieval.scope import Scope
from app.retrieval.vector import VectorHit, vector_search
from app.retrieval.windows import focus_window

_log = get_logger("vault.retrieval")
CACHE_PREFIX = "vault:retr:"


def _sentence_spans(raw: Sequence[object]) -> list[tuple[int, int]]:
    """Stored as [start, end] pairs; tolerate {"start","end"} objects (some test seeds)."""
    out: list[tuple[int, int]] = []
    for item in raw:
        if isinstance(item, dict):
            out.append((int(item["start"]), int(item["end"])))
        else:
            pair: Sequence[int] = item  # type: ignore[assignment]
            out.append((int(pair[0]), int(pair[1])))
    return out


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
    timings: dict[str, int] = field(
        default_factory=dict, compare=False
    )  # embed_ms, vector_ms, ... (not cached)


@asynccontextmanager
async def _use(db: AsyncSession | async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    """A caller-owned session is used as is; a sessionmaker gives a fresh short session per phase."""
    if isinstance(db, AsyncSession):
        yield db
    else:
        async with db() as s:
            yield s


def _ms(t0: float) -> int:
    return int((time.monotonic() - t0) * 1000)


def result_cache_key(
    owner_id: uuid.UUID,
    query: str,
    version_ids: Sequence[uuid.UUID],
    config_version: str,
    embedding_model: str = "",
    chunker_versions: Sequence[str] = (),
) -> str:
    """Besides owner, query, scope versions and config version, the key covers what changes the stored
    chunks or their vectors without changing a version id: the embedding model and the chunker versions."""
    payload = json.dumps(
        [
            str(owner_id),
            normalize_query(query),
            sorted(str(v) for v in set(version_ids)),
            config_version,
            embedding_model,
            sorted(set(chunker_versions)),
        ]
    )
    return CACHE_PREFIX + hashlib.sha256(payload.encode()).hexdigest()


def _encode(r: RetrievalResult) -> str:
    d = asdict(r)
    d.pop("timings", None)
    return json.dumps(d, default=str)


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

    async def warm_up(self) -> None:
        """One tiny rerank request (never raises): the service loads/initializes lazily on first use."""
        await self._reranker.rerank("warm up", [RerankPassage(uuid.UUID(int=0), "warm up")])

    @property
    def config(self) -> RetrievalConfig:
        return self._cfg

    async def embed(self, query: str, owner_id: uuid.UUID) -> QueryEmbedding:
        """Embed a query through the shared cached embedder (the turn starts this while it analyzes)."""
        return await self._embedder.embed(query, owner_id=owner_id)

    async def retrieve(
        self,
        db: AsyncSession | async_sessionmaker[AsyncSession],
        owner_id: uuid.UUID,
        scope: Scope,
        query: str,
        *,
        embedding: QueryEmbedding | None = None,
    ) -> RetrievalResult:
        """With a sessionmaker, every DB phase uses its own short session (no connection is held across the
        reranker call) and the vector and lexical searches run concurrently. With a bare session (legacy
        callers) everything runs sequentially on it. `embedding` is a precomputed embedding of `query`."""
        cfg = self._cfg
        version_ids = [v for _, v in scope.entries]
        if not version_ids or not query.strip():
            return RetrievalResult((), (), (), cfg.version)
        key = await self.cache_key(db, owner_id, query, version_ids)
        cached = await self._cache_get(key) if key else None
        if cached is not None:
            return cached

        timings: dict[str, int] = {}
        degraded: list[str] = []
        t0 = time.monotonic()
        emb = embedding or await self._embedder.embed(query, owner_id=owner_id)
        timings["embed_ms"] = _ms(t0)

        async def vec() -> list[VectorHit]:
            if emb.vector is None:
                return []
            t = time.monotonic()
            async with _use(db) as s:
                hits = await vector_search(
                    s, owner_id, scope, emb.vector, k=cfg.k_vec, per_doc_quota=cfg.per_doc_quota
                )
            timings["vector_ms"] = _ms(t)
            return hits

        async def lex() -> list[LexicalHit]:
            t = time.monotonic()
            async with _use(db) as s:
                out = await lexical_search(s, owner_id, scope, query, k=cfg.k_lex)
            timings["lexical_ms"] = _ms(t)
            return out

        if isinstance(db, AsyncSession):  # one session cannot run concurrent statements
            hits = await vec()
            lex_hits = await lex()
        else:
            hits, lex_hits = await asyncio.gather(vec(), lex())
        distance = {h.chunk_id: h.distance for h in hits}
        vec_ranked = [Ranked(h.chunk_id, h.version_id, 1.0 - h.distance) for h in hits]
        if emb.vector is None and emb.degraded:
            degraded.append(emb.degraded)
        lex_ranked = [Ranked(h.chunk_id, h.version_id, h.score, h.id_match) for h in lex_hits]

        fused = fuse(vec_ranked, lex_ranked, k=cfg.rrf_k)
        pool = fused[: cfg.rerank.candidates]
        async with _use(db) as s:
            texts = await self._passages(s, owner_id, [c.chunk_id for c in pool], query)
            await s.rollback()  # end the read transaction: no connection held across the reranker call
        pool = [c for c in pool if c.chunk_id in texts]  # a chunk deleted mid-request drops out
        t = time.monotonic()
        if cfg.rerank.kind == "none":  # a deliberate no-rerank config (fusion order), not a degraded run
            rr = RerankResult(
                [RerankedItem(c.chunk_id, i, None) for i, c in enumerate(pool[: cfg.rerank.top_n])],
                [], False, None, None, None,
            )  # fmt: skip
        else:
            rr = await self._reranker.rerank(
                query, [RerankPassage(c.chunk_id, texts[c.chunk_id]) for c in pool], cfg.rerank.top_n
            )
        timings["rerank_ms"] = _ms(t)
        if rr.degraded and rr.degraded_reason:
            degraded.append(rr.degraded_reason)
        scores = {c.chunk_id: rr.scores[i] for i, c in enumerate(pool)} if rr.scores else {}
        top_ids = [it.chunk_id for it in rr.items]

        t = time.monotonic()
        final: list[tuple[uuid.UUID, uuid.UUID | None]] = [(cid, None) for cid in top_ids]
        by_id: dict[uuid.UUID, FusedCandidate] = {c.chunk_id: c for c in fused}
        extra: list[tuple[uuid.UUID, uuid.UUID, uuid.UUID | None]] = []  # (chunk, version, expanded_from)
        if cfg.expansion.enabled and top_ids:
            async with _use(db) as s:
                exp = await expand_neighbors(s, owner_id, top_ids, cfg.expansion.max_evidence_tokens)
                final = [(e.chunk_id, e.expanded_from) for e in exp]
                new_ids = [cid for cid, _ in final if cid not in by_id]  # neighbors never retrieved
                versions = await self._versions_of(s, owner_id, new_ids)
                missing = {cid for cid in new_ids if cid not in versions}
                if missing:  # deleted mid-request: drop from the evidence too, so both stay consistent
                    _log.warning("expansion neighbor vanished", count=len(missing))
                    final = [(cid, ef) for cid, ef in final if cid not in missing]
                extra = [(cid, versions[cid], ef) for cid, ef in final if cid in versions]
        timings["expand_ms"] = _ms(t)
        order = {cid: (i, ef) for i, (cid, ef) in enumerate(final)}

        cands = [_candidate(c, distance, scores.get(c.chunk_id), order.get(c.chunk_id)) for c in fused]
        cands += [
            Candidate(cid, vid, selected=True, expanded_from=ef, result_index=order[cid][0])
            for cid, vid, ef in extra
        ]
        result = RetrievalResult(tuple(cands), tuple(cid for cid, _ in final), tuple(degraded), cfg.version)
        if not degraded and emb.vector is not None and not rr.degraded:
            await self._cache_set(key, result)
        return dataclasses.replace(result, timings=timings)

    async def cache_key(
        self,
        db: AsyncSession | async_sessionmaker[AsyncSession],
        owner_id: uuid.UUID,
        query: str,
        version_ids: Sequence[uuid.UUID],
    ) -> str:
        """The result cache key; empty when caching is off (no Redis or zero TTL), which skips the lookup."""
        if self._redis is None or self._cfg.cache_ttl_s == 0:
            return ""
        async with _use(db) as s:
            rows = await s.execute(
                text("SELECT DISTINCT chunker_version FROM document_versions WHERE id = ANY(:v)"),
                {"v": list(version_ids)},
            )
            chunkers = [r[0] or "" for r in rows]
        return result_cache_key(
            owner_id, query, version_ids, self._cfg.version, self._embedder.model, chunkers
        )

    async def _passages(
        self, session: AsyncSession, owner_id: uuid.UUID, ids: Sequence[uuid.UUID], query: str
    ) -> dict[uuid.UUID, str]:
        if not ids:
            return {}
        rows = await session.execute(
            text(
                "SELECT c.id, c.original_text, c.sentences, d.title, c.section_path FROM chunks c "
                "JOIN documents d ON d.id = c.document_id AND d.owner_id = c.owner_id "
                "WHERE c.owner_id = :o AND c.id = ANY(:ids)"
            ),
            {"o": owner_id, "ids": list(ids)},
        )
        out: dict[uuid.UUID, str] = {}
        for cid, original, sents, title, section in rows:
            window = focus_window(original, _sentence_spans(sents), query, self._cfg.rerank.window_words)
            head = f"{title} | {section[-1]}" if section else title
            out[cid] = f"{head[:120]}\n{window}"
        return out

    @staticmethod
    async def _versions_of(
        session: AsyncSession, owner_id: uuid.UUID, chunk_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, uuid.UUID]:
        """Version of each chunk in ONE query (chunks missing from the result no longer exist)."""
        if not chunk_ids:
            return {}
        rows = await session.execute(
            text("SELECT id, version_id FROM chunks WHERE owner_id = :o AND id = ANY(:ids)"),
            {"o": owner_id, "ids": list(chunk_ids)},
        )
        return {cid: vid for cid, vid in rows}

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
