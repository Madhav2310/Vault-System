"""S17.04 identifier tokens at index time: the index stage fills `tsv_ids`; the backfill is idempotent."""

from typing import Any

from sqlalchemy import text

from app.core.settings import get_settings
from app.ingestion.embed_index import IndexStage
from app.ingestion.identifiers import backfill_tsv_ids
from app.ingestion.runner import run_job
from tests.support.docs import seed_document
from tests.support.embed import add_chunks
from tests.support.jobs import make_job, version_status
from tests.support.retrieval import seed_text_chunk

WITH_IDS = "Termination is governed by Section 7.3 and clause 4.2(b); the fee is $1,250,000."
WITHOUT_IDS = "The supplier shall deliver goods promptly and in good order."


async def _tsv(sm: Any, chunk_id: Any) -> str | None:
    async with sm() as s:
        return (
            await s.execute(text("SELECT tsv_ids::text FROM chunks WHERE id = :i"), {"i": chunk_id})
        ).scalar_one()


async def test_tsv_ids_filled(sm: Any, make_owner: Any, store: Any, enqueued: Any) -> None:
    o = await make_owner()
    ref = await make_job(sm, o.id, stage="index")
    ids = await add_chunks(sm, o.id, ref, 2, embedded=True)
    async with sm() as s:
        for cid, body in zip(ids, (WITH_IDS, WITHOUT_IDS), strict=True):
            await s.execute(
                text("UPDATE chunks SET original_text = :t, tsv_ids = NULL WHERE id = :i"),
                {"t": body, "i": cid},
            )
        await s.commit()
    out = await run_job(
        sm,
        ref.job_id,
        "w",
        {"index": IndexStage()},
        enqueued,
        order=("index",),
        services={"store": store, "settings": get_settings()},
    )
    assert out == "finished" and (await version_status(sm, ref.version_id))[0] == "ready"
    filled = await _tsv(sm, ids[0])
    assert (
        filled is not None and "'7.3'" in filled and "'4.2'" in filled
    )  # the parser splits 4.2(b), $1,250,000
    async with sm() as s:  # every query-side identifier token matches the stored vector
        for q in ("7.3", "4.2(b)", "$1,250,000"):
            hit = await s.execute(
                text("SELECT tsv_ids @@ websearch_to_tsquery('simple', :q) FROM chunks WHERE id = :i"),
                {"q": q, "i": ids[0]},
            )
            assert hit.scalar_one() is True, q
    assert await _tsv(sm, ids[1]) is None
    async with sm() as s:  # original_text is untouched
        got = (
            await s.execute(text("SELECT original_text FROM chunks WHERE id = :i"), {"i": ids[0]})
        ).scalar_one()
    assert got == WITH_IDS


async def test_backfill_idempotent(sm: Any, make_owner: Any) -> None:
    o = await make_owner()
    doc = await seed_document(sm, o.id)
    a = await seed_text_chunk(sm, o.id, doc, WITH_IDS, ordinal=0)
    b = await seed_text_chunk(sm, o.id, doc, WITHOUT_IDS, ordinal=1)
    async with sm() as s:
        first = await backfill_tsv_ids(s, owner_id=o.id)
    assert first == 1
    assert await _tsv(sm, a) is not None and await _tsv(sm, b) is None
    async with sm() as s:
        assert await backfill_tsv_ids(s, owner_id=o.id) == 0
