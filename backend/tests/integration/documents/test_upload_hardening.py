"""Regression tests for C1 review findings 2, 6, 9, 11, 12 and 16 (upload and pipeline hardening)."""

import uuid

import httpx
from sqlalchemy import text

from app.ingestion import stages
from app.ingestion.embed_index import EmbedStage
from tests.support.embed import add_chunks, db_gateway, stage_ctx
from tests.support.jobs import make_job
from tests.support.pipeline import run_pipeline, upload
from tests.support.uploads import STREAM_HEADERS, VALID_PDF, multipart

MB = 1024 * 1024


async def test_total_size_capped_without_content_length(api, make_owner) -> None:  # type: ignore[no-untyped-def]
    """A chunked body that streams unlimited bytes in an ignored part used to be read to the end."""
    o = await make_owner()

    async def body():  # type: ignore[no-untyped-def]
        yield (b'--vaulttestboundary7MA4YWxkTrZu0gW\r\nContent-Disposition: form-data; name="junk"\r\n\r\n')
        for _ in range(30):  # 30 MB in an unrelated part, then the file
            yield b"x" * MB

    r = await api.post("/v1/documents", content=body(), headers={**STREAM_HEADERS, **o.auth})
    assert r.status_code == 413 and r.json()["code"] == "file_too_large"


async def test_control_characters_in_filename_and_title(api, make_owner, sm) -> None:  # type: ignore[no-untyped-def]
    o = await make_owner()
    body, h = multipart({"title": "Q3\x00 report\x07"}, ("na\x00me\x1f.pdf", VALID_PDF.read_bytes()))
    r = await api.post("/v1/documents", content=body, headers={**h, **o.auth})
    assert r.status_code == 202, r.text  # was a 500 (Postgres rejects NUL)
    async with sm() as s:
        title, fn = (
            await s.execute(
                text(
                    "SELECT d.title, v.filename FROM documents d "
                    "JOIN document_versions v ON v.document_id = d.id WHERE d.id = :d"
                ),
                {"d": r.json()["document_id"]},
            )
        ).one()
    assert (title, fn) == ("Q3 report", "name.pdf")


async def test_blob_restored_after_purge_race(api, make_owner, sm, store, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Delete then re-upload of the same bytes: a purge deleting the blob file between the upload's write and
    its commit must not leave a version pointing at a missing file."""
    import hashlib

    from app.api import documents

    data = VALID_PDF.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    o = await make_owner()
    real_put = documents.store_blob
    calls = {"n": 0}

    async def racing(st, path, digest):  # type: ignore[no-untyped-def]
        await real_put(st, path, digest)
        calls["n"] += 1
        if calls["n"] == 1:
            await store.delete(f"blobs/{sha}")  # the concurrent purge removes the file right after our write

    monkeypatch.setattr(documents, "store_blob", racing)
    body, h = multipart({}, ("a.pdf", data))
    r = await api.post("/v1/documents", content=body, headers={**h, **o.auth})
    assert r.status_code == 202 and calls["n"] == 2
    assert await store.exists(f"blobs/{sha}")  # re-asserted after the commit


async def test_artifact_written_only_by_a_committing_stage(api, make_owner, sm, store, enqueued) -> None:  # type: ignore[no-untyped-def]
    """The extracted artifact is written inside the fenced commit: a mid-extract delete leaves none."""
    o = await make_owner()
    up = await upload(api, o, "a.pdf", VALID_PDF.read_bytes())
    async with sm() as s:  # the document is deleted before the extract stage commits
        await s.execute(
            text("UPDATE documents SET deleted_at = now() WHERE id = :d"), {"d": up["document_id"]}
        )
        await s.commit()
    outcomes = await run_pipeline(sm, uuid.UUID(up["job_id"]), store, enqueued)
    assert outcomes == ["deleted"]
    assert not await store.exists(f"extracted/{up['version_id']}.json.gz")


async def test_no_paid_embedding_for_a_deleted_document(sm, make_owner, tmp_path) -> None:  # type: ignore[no-untyped-def]
    from app.ingestion.jobs import claim

    o = await make_owner()
    ref = await make_job(sm, o.id, stage="embed")
    await add_chunks(sm, o.id, ref, 5)
    async with sm() as s:
        await s.execute(text("UPDATE documents SET deleted_at = now() WHERE id = :d"), {"d": ref.document_id})
        await s.commit()
    async with sm() as s:
        job = await claim(s, ref.job_id, "w")
    gw, prov = db_gateway(tmp_path, sm)
    await EmbedStage().run(stage_ctx(sm, job, gw))
    assert prov.embed_calls == []


def test_parser_subprocess_gets_no_secrets() -> None:
    assert not {"OPENAI_API_KEY", "DATABASE_URL", "REDIS_URL", "ANTHROPIC_API_KEY"} & set(
        stages._PARSER_ENV_KEYS
    )
    assert "PATH" in stages._PARSER_ENV_KEYS


_ = httpx
