"""Seed embedded chunks with chosen vectors and a pure-python cosine reference (no numpy dependency)."""

import math
import random
import struct
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.ids import uuid7
from tests.support.docs import Seeded

DIM = 1536


def unit_vector(rng: random.Random, dim: int = DIM) -> list[float]:
    v = [rng.gauss(0.0, 1.0) for _ in range(dim)]
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v]


def to_half(v: list[float]) -> list[float]:
    """The values pgvector stores for `halfvec` (float16 rounding)."""
    return list(struct.unpack(f"<{len(v)}e", struct.pack(f"<{len(v)}e", *v)))


def literal(v: list[float]) -> str:
    return "[" + ",".join(repr(x) for x in v) + "]"


def cosine_distance(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return 1.0 - dot / (na * nb)


async def seed_vectors(
    sm: async_sessionmaker[AsyncSession],
    owner_id: uuid.UUID,
    seeded: Seeded,
    vectors: list[list[float] | None],
    *,
    batch: int = 250,
) -> list[uuid.UUID]:
    """One chunk per vector (None = not embedded yet), ordinals 0..; returns chunk ids in order."""
    sql = text(
        "INSERT INTO chunks (id, owner_id, document_id, version_id, ordinal, kind, original_text, "
        "embedding_text, page_start, page_end, sentences, spans, token_count, content_hash, embedding) "
        "VALUES (:id, :o, :d, :v, :ord, 'text', 'x', 'x', 1, 1, CAST('[]' AS jsonb), CAST('[]' AS jsonb), "
        "1, 'h', CAST(:e AS halfvec))"
    )
    ids = [uuid7() for _ in vectors]
    for start in range(0, len(vectors), batch):
        rows = [
            {
                "id": ids[i],
                "o": owner_id,
                "d": seeded.document_id,
                "v": seeded.version_id,
                "ord": i,
                "e": literal(vectors[i]) if vectors[i] is not None else None,  # type: ignore[arg-type]
            }
            for i in range(start, min(start + batch, len(vectors)))
        ]
        async with sm() as s:
            await s.execute(sql, rows)
            await s.commit()
    return ids
