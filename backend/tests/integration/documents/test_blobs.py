"""S1.03 content-addressed blobs with refcount."""

import asyncio
import hashlib

import httpx
from sqlalchemy import text

from tests.support.uploads import VALID_PDF, multipart

DATA = VALID_PDF.read_bytes()
SHA = hashlib.sha256(DATA).hexdigest()


async def _refcount(sm) -> int | None:  # type: ignore[no-untyped-def]
    async with sm() as s:
        return (await s.execute(text("SELECT refcount FROM blobs WHERE sha256 = :s"), {"s": SHA})).scalar()


async def test_key_is_sha256(api: httpx.AsyncClient, make_owner, store) -> None:  # type: ignore[no-untyped-def]
    o = await make_owner()
    body, h = multipart({}, ("a.pdf", DATA))
    assert (await api.post("/v1/documents", content=body, headers={**h, **o.auth})).status_code == 202
    assert await store.exists(f"blobs/{SHA}") and await store.get(f"blobs/{SHA}") == DATA


async def test_shared_blob_refcount(api: httpx.AsyncClient, make_owner, sm) -> None:  # type: ignore[no-untyped-def]
    a, b = await make_owner(), await make_owner()
    body, h = multipart({}, ("a.pdf", DATA))
    for o in (a, b):
        assert (await api.post("/v1/documents", content=body, headers={**h, **o.auth})).status_code == 202
    assert await _refcount(sm) == 2
    # a dedupe hit adds no reference
    assert (await api.post("/v1/documents", content=body, headers={**h, **a.auth})).status_code == 200
    assert await _refcount(sm) == 2


async def test_concurrent_upsert(api: httpx.AsyncClient, make_owner, sm) -> None:  # type: ignore[no-untyped-def]
    owners = [await make_owner() for _ in range(4)]
    body, h = multipart({}, ("a.pdf", DATA))
    rs = await asyncio.gather(
        *(api.post("/v1/documents", content=body, headers={**h, **o.auth}) for o in owners)
    )
    assert [r.status_code for r in rs] == [202] * 4
    async with sm() as s:
        rows = (
            await s.execute(text("SELECT count(*) FROM blobs WHERE sha256 = :s"), {"s": SHA})
        ).scalar_one()
    assert rows == 1 and await _refcount(sm) == 4
