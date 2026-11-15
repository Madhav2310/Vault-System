"""Schema checks against the migrated `vault_test` DB. Expected lists are hard-coded here."""

import re

import pytest
from sqlalchemy import text
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.models import Base

DESIGN_TABLES = {
    "users", "idempotency_keys", "blobs", "documents", "document_versions", "pages", "processing_jobs",
    "job_events", "chunks", "llm_cache", "document_insights", "chat_sessions", "chat_messages", "rag_runs",
    "run_versions", "run_steps", "retrieval_candidates", "answer_segments", "segment_evidence", "llm_calls",
    "feedback", "eval_runs", "eval_results",
}  # fmt: skip

# (table, columns) for each uniqueness the schema promises (primary keys that serve as one are included).
DESIGN_UNIQUES = [
    ("users", ("email",)),
    ("idempotency_keys", ("owner_id", "key")),
    ("document_versions", ("document_id", "version_no")),
    ("pages", ("version_id", "page_no")),
    ("processing_jobs", ("version_id",)),
    ("chunks", ("owner_id", "id")),
    ("chunks", ("owner_id", "version_id", "ordinal")),
    ("llm_cache", ("key",)),
    ("document_insights", ("version_id", "kind", "params_hash", "prompt_version")),
    ("chat_messages", ("session_id", "client_message_id")),
    ("run_versions", ("run_id", "version_id")),
    ("retrieval_candidates", ("run_id", "attempt", "chunk_id")),
    ("segment_evidence", ("segment_id", "chunk_id", "sentence_start")),
    ("feedback", ("owner_id", "run_id", "segment_id", "chunk_id", "kind")),
    ("eval_results", ("eval_run_id", "question_id", "metric")),
]


async def _scalar_rows(engine: AsyncEngine, sql: str, **params: object) -> list[tuple[object, ...]]:
    async with engine.connect() as conn:
        return [tuple(r) for r in (await conn.execute(text(sql), params)).all()]


async def test_all_tables_exist(engine: AsyncEngine) -> None:
    rows = await _scalar_rows(
        engine,
        "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relkind IN ('r','p') AND NOT c.relispartition",
    )
    tables = {r[0] for r in rows} - {"alembic_version"}
    assert tables == DESIGN_TABLES


async def test_chunks_partition_count(engine: AsyncEngine) -> None:
    rows = await _scalar_rows(
        engine,
        "SELECT pg_get_partkeydef('chunks'::regclass), "
        "(SELECT count(*) FROM pg_inherits WHERE inhparent = 'chunks'::regclass)",
    )
    partkey, count = rows[0]
    assert partkey == "HASH (owner_id)"
    assert count == 32


async def test_embedding_halfvec(engine: AsyncEngine) -> None:
    rows = await _scalar_rows(
        engine,
        "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
        "WHERE attrelid = 'chunks'::regclass AND attname = 'embedding'",
    )
    assert rows == [("halfvec(1536)",)]


@pytest.mark.parametrize(
    ("table", "columns"), DESIGN_UNIQUES, ids=lambda v: v if isinstance(v, str) else None
)
async def test_unique_constraints(engine: AsyncEngine, table: str, columns: tuple[str, ...]) -> None:
    rows = await _scalar_rows(
        engine,
        "SELECT array_agg(a.attname ORDER BY k.ord) "
        "FROM pg_constraint c CROSS JOIN LATERAL unnest(c.conkey) WITH ORDINALITY AS k(attnum, ord) "
        "JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum "
        "WHERE c.conrelid = CAST(:t AS regclass) AND c.contype IN ('u','p') GROUP BY c.oid",
        t=table,
    )
    assert tuple(columns) in {tuple(r[0]) for r in rows}


async def test_feedback_unique_nulls_not_distinct(engine: AsyncEngine) -> None:
    rows = await _scalar_rows(
        engine, "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'uq_feedback_key'"
    )
    assert "NULLS NOT DISTINCT" in str(rows[0][0])


async def test_extensions(engine: AsyncEngine) -> None:
    rows = await _scalar_rows(engine, "SELECT extname, extversion FROM pg_extension")
    ext = dict(rows)
    assert "btree_gin" in ext
    assert tuple(int(x) for x in str(ext["vector"]).split(".")) >= (0, 8, 0)


async def test_tsv_en_generated(engine: AsyncEngine) -> None:
    rows = await _scalar_rows(
        engine,
        "SELECT attgenerated, pg_get_expr(d.adbin, d.adrelid) FROM pg_attribute a "
        "JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum "
        "WHERE a.attrelid = 'chunks'::regclass AND a.attname = 'tsv_en'",
    )
    generated, expr = rows[0]
    assert generated in ("s", b"s")  # STORED (pg "char" arrives as bytes via asyncpg)
    assert "to_tsvector('english'::regconfig, embedding_text)" in str(expr)


async def test_gin_indexes_composite(engine: AsyncEngine) -> None:
    rows = await _scalar_rows(engine, "SELECT indexdef FROM pg_indexes WHERE tablename = 'chunks'")
    defs = " | ".join(str(r[0]) for r in rows)
    assert "USING gin (owner_id, tsv_en)" in defs
    assert "USING gin (owner_id, tsv_ids)" in defs


def _norm(t: str) -> str:
    t = t.lower().replace(" ", "")
    return {"timestampwithtimezone": "timestamptz", "doubleprecision": "float8", "real": "float4"}.get(t, t)


async def test_models_match_db(engine: AsyncEngine) -> None:
    """ORM models and migration agree on columns, types and nullability (drift guard)."""
    rows = await _scalar_rows(
        engine,
        "SELECT c.relname, a.attname, format_type(a.atttypid, a.atttypmod), NOT a.attnotnull "
        "FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relkind IN ('r','p') AND NOT c.relispartition "
        "AND a.attnum > 0 AND NOT a.attisdropped AND c.relname <> 'alembic_version'",
    )
    db = {(t, col): (_norm(str(typ)), bool(nullable)) for t, col, typ, nullable in rows}
    dialect = postgresql.dialect()  # type: ignore[no-untyped-call]
    orm = {
        (table.name, col.name): (_norm(col.type.compile(dialect=dialect)), bool(col.nullable))
        for table in Base.metadata.tables.values()
        for col in table.columns
    }
    assert set(db) == set(orm), (
        f"column sets differ: db-only={set(db) - set(orm)} orm-only={set(orm) - set(db)}"
    )
    mismatched = {k: (db[k], orm[k]) for k in db if db[k] != orm[k]}
    assert not mismatched, mismatched
    assert re.fullmatch(r"halfvec\(1536\)", db[("chunks", "embedding")][0])
