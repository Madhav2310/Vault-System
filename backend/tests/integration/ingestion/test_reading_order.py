"""Reading-order repairs reach `pages.flags`."""

import uuid

from sqlalchemy import text

from tests.fixtures.make_fixtures import Page, pdf_bytes
from tests.support.pipeline import run_pipeline, upload


async def test_flags_recorded(api, make_owner, sm, store, enqueued) -> None:  # type: ignore[no-untyped-def]
    page = Page()
    for i in range(8):  # two columns drawn in step: the extractor must repair the order
        page.text(f"Left column sentence number {i} about apples.", 72, 700 - 14 * i, 10)
        page.text(f"Right column sentence number {i} about pears.", 330, 700 - 14 * i, 10)
    up = await upload(api, await make_owner(), "cols.pdf", pdf_bytes([page]))
    await run_pipeline(sm, uuid.UUID(up["job_id"]), store, enqueued)
    async with sm() as s:
        flags = (
            await s.execute(text("SELECT flags FROM pages WHERE version_id = :v"), {"v": up["version_id"]})
        ).scalar_one()
    assert {"multi_column", "reading_order_repaired"} <= set(flags)
