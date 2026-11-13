"""S1.22 AC12: the chunk stage writes chunk rows and records chunker_version on the version."""

import uuid

from sqlalchemy import text

from tests.fixtures.make_fixtures import Page, pdf_bytes
from tests.support.jobs import job_row
from tests.support.pipeline import run_pipeline, upload


def _page(i: int) -> Page:
    p = Page()
    for j in range(20):
        p.text(f"Page {i} sentence {j}: the supplier shall deliver the goods on time.", 72, 720 - 14 * j, 10)
    return p


async def test_version_recorded(api, make_owner, sm, store, enqueued) -> None:  # type: ignore[no-untyped-def]
    up = await upload(api, await make_owner(), "contract.pdf", pdf_bytes([_page(i) for i in range(3)]))
    outcomes = await run_pipeline(sm, uuid.UUID(up["job_id"]), store, enqueued)
    assert outcomes[:3] == ["advanced", "advanced", "advanced"]  # extract, quality, chunk
    assert (await job_row(sm, uuid.UUID(up["job_id"])))["current_stage"] == "embed"
    async with sm() as s:
        cv, tokens = (
            await s.execute(
                text("SELECT chunker_version, token_count FROM document_versions WHERE id = :v"),
                {"v": up["version_id"]},
            )
        ).one()
        rows = (
            await s.execute(
                text(
                    "SELECT ordinal, kind, page_start, page_end, token_count FROM chunks "
                    "WHERE version_id = :v ORDER BY ordinal"
                ),
                {"v": up["version_id"]},
            )
        ).all()
    assert cv == "chunker@1" and rows and tokens == sum(r[4] for r in rows)
    assert [r[0] for r in rows] == list(range(len(rows))) and rows[0][2] == 1 and rows[-1][3] == 3
    # idempotent re-run of the stage: same number of rows
    async with sm() as s:
        await s.execute(
            text("UPDATE processing_jobs SET current_stage = 'chunk', status = 'pending' WHERE id = :j"),
            {"j": up["job_id"]},
        )
        await s.commit()
    await run_pipeline(sm, uuid.UUID(up["job_id"]), store, enqueued, max_steps=1)
    async with sm() as s:
        n = (
            await s.execute(
                text("SELECT count(*) FROM chunks WHERE version_id = :v"), {"v": up["version_id"]}
            )
        ).scalar_one()
    assert n == len(rows)
