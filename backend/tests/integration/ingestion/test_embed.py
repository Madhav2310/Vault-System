"""Embed stage."""

import pathlib

import pytest
from sqlalchemy import text

from app.ingestion.embed_index import EmbedStage
from app.ingestion.errors import RetryableStageError
from app.ingestion.jobs import claim
from app.llm.providers import fake
from tests.support.embed import add_chunks, db_gateway, stage_ctx
from tests.support.jobs import make_job


async def _claimed(sm, owner_id, **kw):  # type: ignore[no-untyped-def]
    ref = await make_job(sm, owner_id, stage="embed", **kw)
    async with sm() as s:
        job = await claim(s, ref.job_id, "w")
    assert job is not None
    return ref, job


async def _embedded(sm, version_id) -> tuple[int, int, set[int]]:  # type: ignore[no-untyped-def]
    async with sm() as s:
        total, filled = (
            await s.execute(
                text("SELECT count(*), count(embedding) FROM chunks WHERE version_id = :v"), {"v": version_id}
            )
        ).one()
        dims = {
            r[0]
            for r in await s.execute(
                text("SELECT vector_dims(embedding) FROM chunks WHERE version_id = :v"), {"v": version_id}
            )
        }
    return total, filled, dims


async def test_batching(sm, make_owner, tmp_path: pathlib.Path) -> None:  # type: ignore[no-untyped-def]
    o = await make_owner()
    ref, job = await _claimed(sm, o.id)
    await add_chunks(sm, o.id, ref, 300)
    gw, prov = db_gateway(tmp_path, sm)
    res = await EmbedStage().run(stage_ctx(sm, job, gw))
    assert len(prov.embed_calls) == 3 and [len(c[1]) for c in prov.embed_calls] == [128, 128, 44]
    assert res.detail and res.detail["calls"] == 3 and res.detail["embedded"] == 300


async def test_cache_skip(sm, make_owner, tmp_path: pathlib.Path) -> None:  # type: ignore[no-untyped-def]
    o = await make_owner()
    ref, job = await _claimed(sm, o.id)
    await add_chunks(sm, o.id, ref, 10)
    gw, prov = db_gateway(tmp_path, sm)
    await EmbedStage().run(stage_ctx(sm, job, gw))
    assert len(prov.embed_calls) == 1
    again = await EmbedStage().run(stage_ctx(sm, job, gw))
    assert len(prov.embed_calls) == 1 and again.detail and again.detail["calls"] == 0  # re-run: zero calls
    # a new version of the same owner with identical chunk text reuses the vectors (content_hash + model)
    ref2, job2 = await _claimed(sm, o.id)
    await add_chunks(sm, o.id, ref2, 10)  # identical texts and hashes
    r2 = await EmbedStage().run(stage_ctx(sm, job2, gw))
    assert len(prov.embed_calls) == 1 and r2.detail and r2.detail["reused"] == 10
    assert (await _embedded(sm, ref2.version_id))[1] == 10


async def test_all_embedded(sm, make_owner, tmp_path: pathlib.Path) -> None:  # type: ignore[no-untyped-def]
    o = await make_owner()
    ref, job = await _claimed(sm, o.id)
    await add_chunks(sm, o.id, ref, 20)
    gw, _ = db_gateway(tmp_path, sm)
    await EmbedStage().run(stage_ctx(sm, job, gw))
    total, filled, dims = await _embedded(sm, ref.version_id)
    assert (total, filled, dims) == (20, 20, {1536})
    async with sm() as s:
        models = (
            (
                await s.execute(
                    text("SELECT DISTINCT embedding_model FROM chunks WHERE version_id = :v"),
                    {"v": ref.version_id},
                )
            )
            .scalars()
            .all()
        )
    assert models == ["text-embedding-3-small"]


async def test_failure_retryable(sm, make_owner, tmp_path: pathlib.Path) -> None:  # type: ignore[no-untyped-def]
    o = await make_owner()
    ref, job = await _claimed(sm, o.id)
    await add_chunks(sm, o.id, ref, 300)
    # batch 1 succeeds, batch 2 fails through every retry and the call is never retried by the stage itself
    script: list[fake.Step] = [None, *[fake.overloaded()] * 3]  # type: ignore[list-item]
    gw, prov = db_gateway(tmp_path, sm, script)
    with pytest.raises(RetryableStageError):
        await EmbedStage().run(stage_ctx(sm, job, gw))
    total, filled, _ = await _embedded(sm, ref.version_id)
    assert (total, filled) == (300, 128)  # finished batches are kept: the retry resumes, it does not repay
    gw2, prov2 = db_gateway(tmp_path, sm)
    await EmbedStage().run(stage_ctx(sm, job, gw2))
    assert [len(c[1]) for c in prov2.embed_calls] == [128, 44]
    assert (await _embedded(sm, ref.version_id))[1] == 300
    _ = prov


async def test_llm_calls_recorded(sm, make_owner, tmp_path: pathlib.Path) -> None:  # type: ignore[no-untyped-def]
    o = await make_owner()
    ref, job = await _claimed(sm, o.id)
    await add_chunks(sm, o.id, ref, 130)
    gw, _ = db_gateway(tmp_path, sm)
    await EmbedStage().run(stage_ctx(sm, job, gw))
    async with sm() as s:
        rows = (
            await s.execute(
                text("SELECT purpose, lane, job_id, owner_id, status FROM llm_calls WHERE job_id = :j"),
                {"j": ref.job_id},
            )
        ).all()
        await s.execute(text("DELETE FROM llm_calls WHERE job_id = :j"), {"j": ref.job_id})
        await s.commit()
    assert len(rows) == 2
    assert all(
        r.purpose == "embed" and r.lane == "background" and r.job_id == ref.job_id and r.status == "ok"
        for r in rows
    )
    assert all(r.owner_id == o.id for r in rows)
