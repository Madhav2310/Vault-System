"""Migration 0006: `chunks.embedding` and `chunks.tsv_en` are stored inline (MAIN) on every partition."""

import asyncio
import random
import uuid

import asyncpg
from alembic import command
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import User
from tests.integration.conftest import alembic_config
from tests.support.docs import seed_document
from tests.support.vectors import literal, unit_vector

SM = async_sessionmaker[AsyncSession]
INLINE = {"embedding": "m", "tsv_en": "m"}
TOASTED = {"embedding": "e", "tsv_en": "x"}  # the type defaults (halfvec EXTERNAL, tsvector EXTENDED)


def _dsn(url: str) -> str:
    return make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)


async def _storage(url: str) -> dict[str, set[str]]:
    """Column -> the set of attstorage values over `chunks` and all its partitions."""
    conn = await asyncpg.connect(_dsn(url))
    try:
        rows = await conn.fetch(
            "SELECT a.attname, a.attstorage::text AS attstorage FROM pg_attribute a "
            "WHERE a.attname IN ('embedding', 'tsv_en') AND (a.attrelid = 'chunks'::regclass "
            "OR a.attrelid IN (SELECT inhrelid FROM pg_inherits WHERE inhparent = 'chunks'::regclass))"
        )
        out: dict[str, set[str]] = {}
        for r in rows:
            out.setdefault(r["attname"], set()).add(r["attstorage"])
        return out
    finally:
        await conn.close()


async def _embeddings(url: str, owner: uuid.UUID) -> list[str]:
    conn = await asyncpg.connect(_dsn(url))
    try:
        rows = await conn.fetch(
            "SELECT embedding::text AS e FROM chunks WHERE owner_id = $1 ORDER BY id", owner
        )
        return [r["e"] for r in rows]
    finally:
        await conn.close()


async def _seed_owner(sm: SM, *, remainder: int | None = None, vectors: int = 0) -> uuid.UUID:
    """A user (whose id hashes to chunks partition `remainder` when given) with one document."""
    async with sm() as s:
        while True:
            uid = uuid.uuid4()
            if remainder is None:
                break
            hit = await s.execute(
                text("SELECT satisfies_hash_partition('chunks'::regclass, 32, :r, CAST(:u AS uuid))"),
                {"r": remainder, "u": uid},
            )
            if hit.scalar():
                break
        s.add(User(id=uid, email=f"{uid}@test.local", api_key_hash=f"h-{uid}"))
        await s.commit()
    doc = await seed_document(sm, uid)
    rng = random.Random(6)
    async with sm() as s:
        for i in range(vectors):
            await s.execute(
                text(
                    "INSERT INTO chunks (id, owner_id, document_id, version_id, ordinal, kind, "
                    "original_text, embedding_text, page_start, page_end, sentences, spans, token_count, "
                    "content_hash, "
                    "embedding) VALUES (gen_random_uuid(), :o, :d, :v, :i, 'text', 'x', 'x', 1, 1, "
                    "CAST('[]' AS jsonb), CAST('[]' AS jsonb), 1, 'h', CAST(:e AS halfvec))"
                ),
                {"o": uid, "d": doc.document_id, "v": doc.version_id, "i": i, "e": literal(unit_vector(rng))},
            )
        await s.commit()
    return uid


def test_every_partition_stores_embedding_and_tsv_inline(db_url: str) -> None:
    assert asyncio.run(_storage(db_url)) == {k: {v} for k, v in INLINE.items()}


async def test_new_partition_inherits_inline_storage_and_keeps_vectors_in_the_heap(
    db_url: str, sm: SM
) -> None:
    owner = await _seed_owner(sm, remainder=31)
    conn = await asyncpg.connect(_dsn(db_url))
    try:
        tx = conn.transaction()
        await tx.start()
        try:  # a fresh partition for remainder 31 (rolled back), then 8 small-text rows with a full vector
            await conn.execute("ALTER TABLE chunks DETACH PARTITION chunks_p31")
            await conn.execute(
                "CREATE TABLE chunks_probe PARTITION OF chunks FOR VALUES WITH (MODULUS 32, REMAINDER 31)"
            )
            storage = await conn.fetch(
                "SELECT attname, attstorage::text AS attstorage FROM pg_attribute "
                "WHERE attrelid = 'chunks_probe'::regclass AND attname IN ('embedding', 'tsv_en')"
            )
            assert {r["attname"]: r["attstorage"] for r in storage} == INLINE
            version = await conn.fetchrow(
                "SELECT d.id AS d, d.current_version_id AS v FROM documents d WHERE d.owner_id = $1", owner
            )
            rng = random.Random(7)
            for i in range(8):
                await conn.execute(
                    "INSERT INTO chunks (id, owner_id, document_id, version_id, ordinal, kind, "
                    "original_text, embedding_text, page_start, page_end, sentences, spans, token_count, "
                    "content_hash, "
                    "embedding) VALUES (gen_random_uuid(), $1, $2, $3, $4, 'text', 'x', 'x', 1, 1, "
                    "'[]', '[]', 1, 'h', $5::halfvec)",
                    owner,
                    version["d"],
                    version["v"],
                    i,
                    literal(unit_vector(rng)),
                )
            sizes = await conn.fetchrow(
                "SELECT pg_relation_size(oid) AS heap, pg_relation_size(reltoastrelid) AS toast "
                "FROM pg_class WHERE relname = 'chunks_probe'"
            )
            assert sizes["toast"] == 0  # EXTERNAL would have moved every 3,076-byte vector out of line
            assert sizes["heap"] >= 3 * 8192  # two ~3.1 kB rows per 8 kB page
        finally:
            await tx.rollback()
    finally:
        await conn.close()


async def test_down_and_up_restore_storage_and_keep_vectors(db_url: str, sm: SM) -> None:
    owner = await _seed_owner(sm, vectors=5)
    before = await _embeddings(db_url, owner)
    cfg = alembic_config(db_url)
    await asyncio.to_thread(command.downgrade, cfg, "0005")
    assert await _storage(db_url) == {k: {v} for k, v in TOASTED.items()}
    assert await _embeddings(db_url, owner) == before
    await asyncio.to_thread(command.upgrade, cfg, "head")
    assert await _storage(db_url) == {k: {v} for k, v in INLINE.items()}
    assert await _embeddings(db_url, owner) == before
