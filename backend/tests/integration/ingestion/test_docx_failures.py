"""S1.18 AC3: a zip bomb fails through the real runner, fast, without full decompression."""

import pathlib
import time

from app.ingestion.extract.docx import DocxExtractor
from app.ingestion.runner import StageContext, StageResult, run_job
from tests.support.jobs import job_row, make_job, version_status

BOMB = pathlib.Path(__file__).resolve().parents[2] / "fixtures" / "files" / "zip_bomb.docx"


class ExtractDocx:
    name = "extract"

    async def run(self, ctx: StageContext) -> StageResult:
        DocxExtractor().extract(str(BOMB))
        return StageResult()


async def test_zip_bomb(sm, make_owner, enqueued) -> None:  # type: ignore[no-untyped-def]
    ref = await make_job(sm, (await make_owner()).id)
    t0 = time.monotonic()
    out = await run_job(sm, ref.job_id, "w", {"extract": ExtractDocx()}, enqueued, order=("extract",))
    elapsed = time.monotonic() - t0
    assert out == "dead"
    assert (await job_row(sm, ref.job_id))["error_code"] == "unsafe_archive"
    assert await version_status(sm, ref.version_id) == ("failed", "unsafe_archive")
    assert elapsed < 2
