"""Contextual index text for both legs (deterministic and llm arms), offline.

The header is index-only: `chunks.original_text`, `sentences` and `spans` (everything a citation is copied
from) never contain it, while the vector input and `tsv_en` do."""

import json
import pathlib
import uuid
from typing import Any

from sqlalchemy import text

from app.core.settings import get_settings
from app.ingestion.contextualize import ContextualizeStage, backfill_owner
from app.ingestion.runner import run_job
from app.llm.gateway import DbLedgerWriter
from app.llm.providers import fake
from tests.fixtures.make_fixtures import Page, pdf_bytes
from tests.support.chat_gen import fake_gateway
from tests.support.embed import add_chunks, db_gateway
from tests.support.jobs import make_job
from tests.support.pipeline import run_pipeline, upload

MARK = "Zyxwvut"


def _pdf() -> bytes:
    p = Page()
    for j in range(12):
        p.text(f"Sentence {j}: the supplier shall deliver the goods on time.", 72, 720 - 14 * j, 10)
    return pdf_bytes([p])


async def _chunks(sm: Any, version_id: Any) -> list[Any]:
    async with sm() as s:
        return list(
            await s.execute(
                text(
                    "SELECT structural_prefix, original_text, sentences, spans, contextual_prefix, "
                    "embedding_text, content_hash, "
                    f"tsv_en @@ plainto_tsquery('english'::regconfig, '{MARK}') AS hit FROM chunks "
                    "WHERE version_id = :v ORDER BY ordinal"
                ),
                {"v": version_id},
            )
        )


async def _rechunk(sm: Any, store: Any, enqueued: Any, up: Any, mode: str) -> None:
    async with sm() as s:
        await s.execute(
            text("UPDATE processing_jobs SET current_stage = 'chunk', status = 'pending' WHERE id = :j"),
            {"j": up["job_id"]},
        )
        await s.commit()
    settings = get_settings().model_copy(update={"index_context": mode})
    await run_pipeline(sm, uuid.UUID(up["job_id"]), store, enqueued, settings=settings, max_steps=1)


async def _ingest(api: Any, make_owner: Any, sm: Any, store: Any, enqueued: Any, mode: str) -> Any:
    up = await upload(api, await make_owner(), "contract.pdf", _pdf())
    async with sm() as s:
        await s.execute(
            text("UPDATE documents SET title = :t WHERE id = :d"),
            {"t": f"{MARK} Handbook", "d": up["document_id"]},
        )
        await s.commit()
    settings = get_settings().model_copy(update={"index_context": mode})
    await run_pipeline(sm, uuid.UUID(up["job_id"]), store, enqueued, settings=settings, max_steps=3)
    return up


async def test_never_cited(api: Any, make_owner: Any, sm: Any, store: Any, enqueued: Any) -> None:
    up = await _ingest(api, make_owner, sm, store, enqueued, "deterministic")
    rows = await _chunks(sm, up["version_id"])
    assert rows
    for r in rows:
        assert r.contextual_prefix and MARK in r.contextual_prefix
        # everything a citation can be copied from is free of the header
        assert r.contextual_prefix not in r.original_text and MARK not in r.original_text
        for s, e in r.sentences:
            assert MARK not in r.original_text[s:e]
        assert MARK not in json.dumps(r.spans)
        # both legs see it: the vector input and the generated tsvector
        assert r.contextual_prefix in r.embedding_text and r.hit
        assert r.embedding_text.endswith("\n" + r.original_text)


async def test_none_is_unchanged(api: Any, make_owner: Any, sm: Any, store: Any, enqueued: Any) -> None:
    up = await _ingest(api, make_owner, sm, store, enqueued, "none")
    for r in await _chunks(sm, up["version_id"]):
        assert r.contextual_prefix is None
        assert r.embedding_text == f"{r.structural_prefix}\n{r.original_text}"  # the chunker's text


async def test_deterministic_idempotent_and_hash_moves(
    api: Any, make_owner: Any, sm: Any, store: Any, enqueued: Any
) -> None:
    up = await _ingest(api, make_owner, sm, store, enqueued, "deterministic")
    first = await _chunks(sm, up["version_id"])
    await _rechunk(sm, store, enqueued, up, "deterministic")
    assert [(r.embedding_text, r.content_hash) for r in await _chunks(sm, up["version_id"])] == [
        (r.embedding_text, r.content_hash) for r in first
    ]
    await _rechunk(sm, store, enqueued, up, "none")  # switching the setting changes the hash
    after = await _chunks(sm, up["version_id"])
    assert all(a.content_hash != b.content_hash for a, b in zip(after, first, strict=True))


async def _table_world(sm: Any, make_owner: Any) -> tuple[Any, Any]:
    o = await make_owner()
    ref = await make_job(sm, o.id, stage="contextualize")
    await add_chunks(sm, o.id, ref, 2, embedded=True)
    prefix = "Document: T | Section: Fin | Table: Table 3 Revenue"
    async with sm() as s:
        await s.execute(
            text("UPDATE document_versions SET status = 'ready' WHERE id = :v"), {"v": ref.version_id}
        )
        await s.execute(
            text("UPDATE documents SET current_version_id = :v WHERE id = :d"),
            {"v": ref.version_id, "d": ref.document_id},
        )
        await s.execute(
            text("UPDATE documents SET title = 'Annual Report' WHERE id = :d"), {"d": ref.document_id}
        )
        await s.execute(
            text(
                "UPDATE chunks SET structural_prefix = :p, section_path = '{Fin}', "
                "embedding_text = :p || E'\\nHeader: Item | 2024\\n' || original_text WHERE version_id = :v"
            ),
            {"p": prefix, "v": ref.version_id},
        )
        await s.execute(
            text("UPDATE chunks SET kind = 'table' WHERE version_id = :v AND ordinal = 0"),
            {"v": ref.version_id},
        )
        await s.commit()
    return o, ref


async def test_backfill_deterministic_without_llm(sm: Any, make_owner: Any, tmp_path: pathlib.Path) -> None:
    o, ref = await _table_world(sm, make_owner)
    gw, prov = db_gateway(tmp_path, sm)
    st = get_settings()
    first = await backfill_owner(sm, gw, st, o.id, "index_deterministic")
    assert first.chunks_changed == 2 and first.failed == 0 and not prov.requests  # no completion call
    async with sm() as s:
        rows = list(
            await s.execute(
                text(
                    "SELECT kind, original_text, contextual_prefix, embedding_text FROM chunks "
                    "WHERE version_id = :v ORDER BY ordinal"
                ),
                {"v": ref.version_id},
            )
        )
    table, prose = rows
    assert "Table: Table 3 Revenue." in table.contextual_prefix
    assert "Columns: Item | 2024." in table.contextual_prefix
    assert "Columns" not in prose.contextual_prefix and "Table:" not in prose.contextual_prefix
    for r in rows:
        assert r.contextual_prefix not in r.original_text
        assert "Header: Item | 2024" in r.embedding_text  # chunker's own line kept
    second = await backfill_owner(sm, gw, st, o.id, "index_deterministic")
    assert second.chunks_changed == 0


async def test_llm_arm_batched_and_capped(
    sm: Any, make_owner: Any, store: Any, enqueued: Any, tmp_path: pathlib.Path
) -> None:
    o = await make_owner()
    ref = await make_job(sm, o.id, stage="contextualize")
    await add_chunks(sm, o.id, ref, 6, embedded=True)
    async with sm() as s:
        await s.execute(
            text("UPDATE document_versions SET status = 'enriched' WHERE id = :v"), {"v": ref.version_id}
        )
        await s.execute(
            text("UPDATE chunks SET structural_prefix = 'Document: T | Section: -' WHERE version_id = :v"),
            {"v": ref.version_id},
        )
        await s.commit()
    gw, prov = fake_gateway(
        [fake.structured({"context": f"Header {i} about delivery."}) for i in range(6)],
        ledger=DbLedgerWriter(sm),
    )

    async def run(**kw: Any) -> str:
        settings = get_settings().model_copy(update={"index_context": "llm", **kw})
        return await run_job(
            sm, ref.job_id, "w", {"contextualize": ContextualizeStage()}, enqueued,
            services={"gateway": gw, "settings": settings, "store": store},
        )  # fmt: skip

    assert await run(contextualize_max_usd=0.0) == "finished"  # cap reached: no call at all
    assert not prov.requests
    async with sm() as s:
        await s.execute(
            text(
                "UPDATE processing_jobs SET status = 'pending', current_stage = 'contextualize' WHERE id = :j"
            ),
            {"j": ref.job_id},
        )
        await s.commit()
    assert await run(contextualize_max_usd=1.0) == "finished"
    assert len(prov.requests) == 6  # one fast-role call per chunk, issued in concurrent waves
    async with sm() as s:
        rows = list(
            await s.execute(
                text(
                    "SELECT original_text, contextual_prefix, embedding_text FROM chunks "
                    "WHERE version_id = :v"
                ),
                {"v": ref.version_id},
            )
        )
    for r in rows:
        assert r.contextual_prefix and r.contextual_prefix in r.embedding_text
        assert r.contextual_prefix not in r.original_text
