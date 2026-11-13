"""S1.04 document/version/job creation with per-owner dedupe."""

import asyncio
import pathlib

import httpx
import pytest
from sqlalchemy import text

from app.documents import service
from tests.support.uploads import VALID_PDF, docx_bytes, multipart

TABLES = ("documents", "document_versions", "processing_jobs")


async def _counts(sm, owner_id) -> dict[str, int]:  # type: ignore[no-untyped-def]
    async with sm() as s:
        return {
            t: (
                await s.execute(text(f"SELECT count(*) FROM {t} WHERE owner_id = :o"), {"o": owner_id})
            ).scalar_one()
            for t in TABLES
        }


async def _post(api, owner, name: str, data: bytes, **fields: str):  # type: ignore[no-untyped-def]
    body, h = multipart(fields, (name, data))
    return await api.post("/v1/documents", content=body, headers={**h, **owner.auth})


async def test_creates_three_rows(api: httpx.AsyncClient, make_owner, sm) -> None:  # type: ignore[no-untyped-def]
    o = await make_owner()
    r = await _post(api, o, "a.pdf", VALID_PDF.read_bytes())
    assert r.status_code == 202
    assert await _counts(sm, o.id) == dict.fromkeys(TABLES, 1)
    async with sm() as s:
        v = (
            await s.execute(
                text("SELECT version_no, status, mime FROM document_versions WHERE owner_id = :o"),
                {"o": o.id},
            )
        ).one()
        j = (
            await s.execute(
                text(
                    "SELECT status, current_stage, version_id::text FROM processing_jobs WHERE owner_id = :o"
                ),
                {"o": o.id},
            )
        ).one()
    assert tuple(v) == (1, "uploaded", "application/pdf")
    assert tuple(j) == ("pending", "extract", r.json()["version_id"])


async def test_dedupe_same_owner(api: httpx.AsyncClient, make_owner, sm) -> None:  # type: ignore[no-untyped-def]
    o = await make_owner()
    first = await _post(api, o, "a.pdf", VALID_PDF.read_bytes())
    again = await _post(api, o, "renamed.pdf", VALID_PDF.read_bytes(), title="Other title")
    assert again.status_code == 200 and again.json()["deduplicated"] is True
    assert again.json()["version_id"] == first.json()["version_id"]
    assert await _counts(sm, o.id) == dict.fromkeys(TABLES, 1)
    # concurrent identical uploads by one owner still create one version
    o2 = await make_owner()
    rs = await asyncio.gather(*(_post(api, o2, "c.pdf", VALID_PDF.read_bytes()) for _ in range(3)))
    assert sorted(r.status_code for r in rs) == [200, 200, 202]
    assert len({r.json()["version_id"] for r in rs}) == 1
    assert await _counts(sm, o2.id) == dict.fromkeys(TABLES, 1)


async def test_no_cross_owner_dedupe(api: httpx.AsyncClient, make_owner, sm) -> None:  # type: ignore[no-untyped-def]
    a, b = await make_owner(), await make_owner()
    ra = await _post(api, a, "a.pdf", VALID_PDF.read_bytes())
    rb = await _post(api, b, "a.pdf", VALID_PDF.read_bytes())
    assert rb.status_code == 202 and rb.json()["deduplicated"] is False
    assert rb.json()["document_id"] != ra.json()["document_id"]
    async with sm() as s:
        owner = (
            await s.execute(
                text("SELECT owner_id FROM documents WHERE id = :d"), {"d": rb.json()["document_id"]}
            )
        ).scalar_one()
    assert owner == b.id


async def test_atomic_rows(
    api: httpx.AsyncClient, make_owner, sm, store, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    o = await make_owner()
    real = service.create_from_upload

    async def boom(*a, **kw):  # type: ignore[no-untyped-def]
        await real(*a, **kw)  # rows and the blob reference are added, then the request fails before commit
        raise RuntimeError("crash before commit")

    monkeypatch.setattr("app.api.documents.create_from_upload", boom)
    data = docx_bytes("atomicity probe")
    r = await _post(api, o, "a.docx", data)
    assert r.status_code == 500
    assert await _counts(sm, o.id) == dict.fromkeys(TABLES, 0)
    import hashlib

    sha = hashlib.sha256(data).hexdigest()
    async with sm() as s:
        assert (
            await s.execute(text("SELECT refcount FROM blobs WHERE sha256 = :s"), {"s": sha})
        ).scalar() is None
    assert await store.exists(f"blobs/{sha}")  # the content-addressed file may remain; it is unreferenced


async def test_default_title(api: httpx.AsyncClient, make_owner, sm) -> None:  # type: ignore[no-untyped-def]
    o = await make_owner()
    r1 = await _post(api, o, "Q3 Board Report.final.pdf", VALID_PDF.read_bytes())
    r2 = await _post(api, o, "memo.docx", docx_bytes(), title="  Custom title  ")
    async with sm() as s:
        titles = dict(
            (
                await s.execute(
                    text("SELECT id::text, title FROM documents WHERE owner_id = :o"), {"o": o.id}
                )
            ).all()
        )
    assert titles[r1.json()["document_id"]] == "Q3 Board Report.final"
    assert titles[r2.json()["document_id"]] == "Custom title"
    assert pathlib.PurePath("x").stem == "x"
