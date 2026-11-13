"""S1.07 CAS dispatch."""

import asyncio
import uuid

from app.ingestion.jobs import dispatch
from tests.support.jobs import job_row, make_job
from tests.support.uploads import VALID_PDF, multipart


async def test_single_enqueue(api, make_owner, enqueued) -> None:  # type: ignore[no-untyped-def]
    o = await make_owner()
    body, h = multipart({}, ("a.pdf", VALID_PDF.read_bytes()))
    r = await api.post("/v1/documents", content=body, headers={**h, **o.auth, "Idempotency-Key": "d-1"})
    job_id = uuid.UUID(r.json()["job_id"])
    # a replay of the same request and a dedupe hit must not enqueue again
    await api.post("/v1/documents", content=body, headers={**h, **o.auth, "Idempotency-Key": "d-1"})
    await api.post("/v1/documents", content=body, headers={**h, **o.auth})
    assert enqueued.calls == [("extract", job_id)]


async def test_concurrent_dispatch_one_winner(sm, make_owner, enqueued) -> None:  # type: ignore[no-untyped-def]
    o = await make_owner()
    ref = await make_job(sm, o.id)

    async def one() -> bool:
        async with sm() as s:
            return await dispatch(s, ref.job_id, enqueued)

    wins = await asyncio.gather(*(one() for _ in range(8)))
    assert sum(wins) == 1 and len(enqueued.calls) == 1
    assert (await job_row(sm, ref.job_id))["status"] == "dispatched"


async def test_dispatch_within_grace_noop(sm, make_owner, enqueued) -> None:  # type: ignore[no-untyped-def]
    o = await make_owner()
    ref = await make_job(sm, o.id)
    async with sm() as s:
        assert await dispatch(s, ref.job_id, enqueued) is True
    async with sm() as s:
        assert await dispatch(s, ref.job_id, enqueued) is False  # inside the 5-min grace
    async with sm() as s:
        assert await dispatch(s, ref.job_id, enqueued, grace_s=0) is True  # past the grace (here: zero)
    assert len(enqueued.calls) == 2
