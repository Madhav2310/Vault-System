"""S1.19 page quality, flags and needs_ocr, through the real extract and quality stages."""

import uuid

from sqlalchemy import text

from tests.fixtures.make_fixtures import Page, pdf_bytes
from tests.support.jobs import job_row, version_status
from tests.support.pipeline import run_pipeline, upload
from tests.support.uploads import FIXTURES


def _text_page(i: int) -> Page:
    p = Page()
    for j in range(6):
        p.text(f"Page {i} line {j}: the supplier delivers goods under clause {j}.", 72, 700 - 14 * j, 10)
    return p


async def _pages(sm, version_id: str) -> list[tuple[int, float, float, int, list[str]]]:  # type: ignore[no-untyped-def]
    async with sm() as s:
        rows = await s.execute(
            text(
                "SELECT page_no, width, height, text_chars, flags FROM pages WHERE version_id = :v "
                "ORDER BY page_no"
            ),
            {"v": version_id},
        )
        return [tuple(r) for r in rows]  # type: ignore[misc]


async def test_scanned_needs_ocr(api, make_owner, sm, store, enqueued) -> None:  # type: ignore[no-untyped-def]
    up = await upload(
        api, await make_owner(), "scan.pdf", (FIXTURES / "files" / "empty_text.pdf").read_bytes()
    )
    outcomes = await run_pipeline(sm, uuid.UUID(up["job_id"]), store, enqueued)
    assert outcomes == [
        "advanced",
        "finished",
    ]  # extract, then quality ends the pipeline (terminal, no retries)
    assert (await version_status(sm, uuid.UUID(up["version_id"])))[0] == "needs_ocr"
    assert (await job_row(sm, uuid.UUID(up["job_id"])))["status"] == "succeeded"
    async with sm() as s:
        chunks = (
            await s.execute(
                text("SELECT count(*) FROM chunks WHERE version_id = :v"), {"v": up["version_id"]}
            )
        ).scalar_one()
    assert chunks == 0
    assert "needs_ocr" in (await _pages(sm, up["version_id"]))[0][4]


async def test_partial_ocr_flag(api, make_owner, sm, store, enqueued) -> None:  # type: ignore[no-untyped-def]
    pages = [_text_page(i) for i in range(10)]
    pages[4] = Page(image=True)  # one image-only page out of ten
    up = await upload(api, await make_owner(), "mixed.pdf", pdf_bytes(pages))
    outcomes = await run_pipeline(sm, uuid.UUID(up["job_id"]), store, enqueued)
    # extract and quality succeed and the job moves on to `chunk` (not terminal). Reaching `ready` is checked
    # end to end once chunk/embed/index exist (C1 checkpoint).
    assert outcomes[:2] == ["advanced", "advanced"]
    assert (await job_row(sm, uuid.UUID(up["job_id"])))["current_stage"] == "chunk"
    assert (await version_status(sm, uuid.UUID(up["version_id"])))[0] == "processing"
    flagged = [p[0] for p in await _pages(sm, up["version_id"]) if "needs_ocr" in p[4]]
    assert flagged == [5]


async def test_pages_rows(api, make_owner, sm, store, enqueued) -> None:  # type: ignore[no-untyped-def]
    up = await upload(api, await make_owner(), "three.pdf", pdf_bytes([_text_page(i) for i in range(3)]))
    await run_pipeline(sm, uuid.UUID(up["job_id"]), store, enqueued)
    rows = await _pages(sm, up["version_id"])
    assert [r[0] for r in rows] == [1, 2, 3]
    assert all(r[1] == 612 and r[2] == 792 and r[3] > 50 for r in rows)
    async with sm() as s:
        n = (
            await s.execute(
                text("SELECT page_count FROM document_versions WHERE id = :v"), {"v": up["version_id"]}
            )
        ).scalar_one()
    assert n == 3
