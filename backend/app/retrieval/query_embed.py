"""Query embedding with a Redis cache and lexical fallback (design 7.3, 7.4).

The cache key is sha256(model + normalized query); the vector is cached for 7 days. The embedding call goes
through the gateway. If it fails, the result carries no vector and `degraded="lexical_only"` so the caller
runs lexical search alone. A Redis failure never fails the request: the cache is bypassed.

Vectors are stored as little-endian float16 (3 KB for 1536 dims versus about 12 KB as JSON). Chunk vectors are
`halfvec` already, so float16 loses nothing the index sees; the freshly embedded vector is quantized the same
way, so a cache hit and a miss give identical query vectors (and identical rankings).
"""

import hashlib
import logging
import struct
import unicodedata
import uuid
from dataclasses import dataclass

import redis.asyncio as aioredis
from redis.exceptions import RedisError

from app.llm.errors import LlmError
from app.llm.gateway import LlmGateway

DEGRADED_LEXICAL_ONLY = "lexical_only"
CACHE_TTL_SECONDS = 7 * 24 * 3600
DEFAULT_KEY_PREFIX = "qembed:v1:"
CACHE_REDIS_TIMEOUT_S = 0.25  # a stalled Redis costs each cache command this, not redis-py's 5 s default

_log = logging.getLogger(__name__)


def cache_client(redis_url: str) -> aioredis.Redis:
    """The client of the query-embedding and retrieval caches: short timeouts, because a miss is cheap."""
    return aioredis.from_url(
        redis_url, socket_connect_timeout=CACHE_REDIS_TIMEOUT_S, socket_timeout=CACHE_REDIS_TIMEOUT_S
    )


@dataclass(frozen=True)
class QueryEmbedding:
    vector: list[float] | None
    degraded: str | None = None
    cache_hit: bool = False

    @property
    def lexical_fallback(self) -> bool:
        return self.degraded == DEGRADED_LEXICAL_ONLY


def normalize_query(query: str) -> str:
    """NFKC, lowercased, whitespace collapsed. Used for the cache key only; the original text is embedded."""
    return " ".join(unicodedata.normalize("NFKC", query).split()).lower()


def cache_key(prefix: str, normalized: str, model: str) -> str:
    return prefix + hashlib.sha256(f"{model}\x00{normalized}".encode()).hexdigest()


def _pack(vector: list[float]) -> bytes:
    return struct.pack(f"<{len(vector)}e", *vector)


def _unpack(blob: bytes) -> list[float]:
    return list(struct.unpack(f"<{len(blob) // 2}e", blob))


class QueryEmbedder:
    """`redis` may be None (no cache). `key_prefix` is injectable so tests can isolate their keys."""

    def __init__(
        self,
        gateway: LlmGateway,
        redis: aioredis.Redis | None,
        *,
        key_prefix: str = DEFAULT_KEY_PREFIX,
        ttl_seconds: int = CACHE_TTL_SECONDS,
    ) -> None:
        self._gw = gateway
        self._redis = redis
        self._prefix = key_prefix
        self._ttl = ttl_seconds

    @property
    def gateway(self) -> LlmGateway:
        return self._gw

    @property
    def model(self) -> str:
        return self._gw.settings.embedding_model

    async def embed(self, query: str, *, owner_id: uuid.UUID | None) -> QueryEmbedding:
        model = self._gw.settings.embedding_model
        dims = self._gw.settings.embedding_dim
        key = cache_key(self._prefix, normalize_query(query), model)

        blob = await self._get(key)
        if blob is not None and len(blob) == 2 * dims:
            return QueryEmbedding(_unpack(blob), cache_hit=True)

        try:
            res = await self._gw.embed(
                purpose="embed_query", lane="interactive", texts=[query], owner_id=owner_id, model=model
            )
        except LlmError:
            return QueryEmbedding(None, degraded=DEGRADED_LEXICAL_ONLY)
        packed = _pack(res.vectors[0])
        await self._set(key, packed)
        return QueryEmbedding(_unpack(packed))

    async def _get(self, key: str) -> bytes | None:
        if self._redis is None:
            return None
        try:
            value = await self._redis.get(key)
        except (RedisError, OSError):
            _log.warning("query embedding cache unavailable (get)")
            return None
        return value if isinstance(value, bytes) else None

    async def _set(self, key: str, blob: bytes) -> None:
        if self._redis is None:
            return
        try:
            await self._redis.set(key, blob, ex=self._ttl)
        except (RedisError, OSError):
            _log.warning("query embedding cache unavailable (set)")
