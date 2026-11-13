"""S1.17 PDF failure classes and the parser timeout, through the real extract stage (subprocess)."""

import uuid

from app.core.settings import get_settings
from tests.support.jobs import job_row, version_status
from tests.support.pipeline import run_pipeline, upload
from tests.support.uploads import FIXTURES

FILES = FIXTURES / "files"


async def test_encrypted(api, make_owner, sm, store, enqueued) -> None:  # type: ignore[no-untyped-def]
    up = await upload(api, await make_owner(), "locked.pdf", (FILES / "encrypted.pdf").read_bytes())
    job_id = uuid.UUID(up["job_id"])
    assert await run_pipeline(sm, job_id, store, enqueued) == ["dead"]
    assert await version_status(sm, uuid.UUID(up["version_id"])) == ("unsupported", "encrypted")


async def test_corrupt_no_retry(api, make_owner, sm, store, enqueued) -> None:  # type: ignore[no-untyped-def]
    up = await upload(api, await make_owner(), "broken.pdf", (FILES / "corrupt_truncated.pdf").read_bytes())
    job_id = uuid.UUID(up["job_id"])
    assert await run_pipeline(sm, job_id, store, enqueued) == ["dead"]
    row = await job_row(sm, job_id)
    assert row["attempt_count"] == 1 and row["status"] == "dead"  # permanent: no retries
    assert await version_status(sm, uuid.UUID(up["version_id"])) == ("failed", "corrupt_file")


async def test_timeout(api, make_owner, sm, store, enqueued) -> None:  # type: ignore[no-untyped-def]
    up = await upload(
        api, await make_owner(), "ok.pdf", (FIXTURES / "geometry" / "ligatures.pdf").read_bytes()
    )
    settings = get_settings().model_copy(
        update={"parse_timeout_s": 0}
    )  # forced low: the subprocess is killed
    assert await run_pipeline(sm, uuid.UUID(up["job_id"]), store, enqueued, settings=settings) == ["dead"]
    assert await version_status(sm, uuid.UUID(up["version_id"])) == ("failed", "parse_timeout")
