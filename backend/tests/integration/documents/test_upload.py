"""Upload endpoint and type rejection over HTTP."""

import hashlib
import pathlib
import tempfile
import tracemalloc
from collections.abc import Iterator

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.support.uploads import STREAM_HEADERS, VALID_PDF, multipart, png_bytes, streamed_file_body

MB = 1024 * 1024


@pytest.fixture
def upload_tmp(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[pathlib.Path]:
    d = tmp_path / "upload-tmp"
    d.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(d))
    yield d


def _files(d: pathlib.Path) -> list[str]:
    return sorted(x.name for x in d.iterdir())


async def _count(sm: async_sessionmaker[AsyncSession], owner_id: object) -> dict[str, int]:
    async with sm() as s:
        out = {}
        for t in ("documents", "document_versions", "processing_jobs"):
            out[t] = (
                await s.execute(text(f"SELECT count(*) FROM {t} WHERE owner_id = :o"), {"o": owner_id})
            ).scalar_one()
        return out


async def test_upload_pdf_202(api: httpx.AsyncClient, make_owner) -> None:  # type: ignore[no-untyped-def]
    o = await make_owner()
    body, headers = multipart({}, ("report.pdf", VALID_PDF.read_bytes()))
    r = await api.post("/v1/documents", content=body, headers={**headers, **o.auth})
    assert r.status_code == 202, r.text
    j = r.json()
    assert j["status"] == "uploaded" and j["deduplicated"] is False
    assert {"document_id", "version_id", "job_id", "links"} <= j.keys()
    assert j["links"]["processing"] == f"/v1/versions/{j['version_id']}/processing"


async def test_oversize_413_no_rows(api: httpx.AsyncClient, make_owner, sm, store) -> None:  # type: ignore[no-untyped-def]
    o = await make_owner()
    r = await api.post(
        "/v1/documents", content=streamed_file_body("big.pdf", 26 * MB), headers={**STREAM_HEADERS, **o.auth}
    )
    assert r.status_code == 413 and r.json()["code"] == "file_too_large"
    assert await _count(sm, o.id) == {"documents": 0, "document_versions": 0, "processing_jobs": 0}
    assert not any((store.root / "blobs").glob("*")) if (store.root / "blobs").exists() else True
    # a declared Content-Length over the cap is rejected before the body is read
    r = await api.post(
        "/v1/documents", content=b"x", headers={**STREAM_HEADERS, **o.auth, "Content-Length": str(30 * MB)}
    )
    assert r.status_code == 413


async def test_sha256_correct(api: httpx.AsyncClient, make_owner, sm) -> None:  # type: ignore[no-untyped-def]
    o = await make_owner()
    data = VALID_PDF.read_bytes()
    body, headers = multipart({}, ("a.pdf", data))
    r = await api.post("/v1/documents", content=body, headers={**headers, **o.auth})
    async with sm() as s:
        sha = (
            await s.execute(
                text("SELECT sha256 FROM document_versions WHERE id = :v"), {"v": r.json()["version_id"]}
            )
        ).scalar_one()
    assert sha == hashlib.sha256(data).hexdigest()


async def test_streaming_memory_bounded(api: httpx.AsyncClient, make_owner) -> None:  # type: ignore[no-untyped-def]
    """Python heap growth while a 25 MB upload streams through (tracemalloc peak, a stricter proxy for RSS:
    the test client's own buffers are generated lazily, so they don't count either)."""
    o = await make_owner()
    tracemalloc.start()
    try:
        tracemalloc.reset_peak()
        base = tracemalloc.get_traced_memory()[0]
        r = await api.post(
            "/v1/documents",
            content=streamed_file_body("max.pdf", 25 * MB),
            headers={**STREAM_HEADERS, **o.auth},
        )
        peak = tracemalloc.get_traced_memory()[1] - base
    finally:
        tracemalloc.stop()
    assert r.status_code == 202, r.text
    assert peak < 10 * MB, f"peak {peak / MB:.1f} MB"


async def test_tempfile_cleanup(api: httpx.AsyncClient, make_owner, upload_tmp: pathlib.Path) -> None:  # type: ignore[no-untyped-def]
    o = await make_owner()
    ok, h = multipart({}, ("ok.pdf", VALID_PDF.read_bytes()))
    bad, _ = multipart({}, ("bad.pdf", png_bytes()))
    for body, expected in ((ok, 202), (bad, 415)):
        r = await api.post("/v1/documents", content=body, headers={**h, **o.auth})
        assert r.status_code == expected
        assert _files(upload_tmp) == []
    r = await api.post(
        "/v1/documents", content=streamed_file_body("big.pdf", 26 * MB), headers={**STREAM_HEADERS, **o.auth}
    )
    assert r.status_code == 413 and _files(upload_tmp) == []


async def test_missing_file_422(api: httpx.AsyncClient, make_owner) -> None:  # type: ignore[no-untyped-def]
    o = await make_owner()
    body, headers = multipart({"title": "no file"}, None)
    r = await api.post("/v1/documents", content=body, headers={**headers, **o.auth})
    assert r.status_code == 422 and r.json()["code"] == "validation_error"
    r = await api.post("/v1/documents", json={"file": "x"}, headers=o.auth)  # not multipart
    assert r.status_code == 422


async def test_spoofed_415(api: httpx.AsyncClient, make_owner, sm) -> None:  # type: ignore[no-untyped-def]
    o = await make_owner()
    body, headers = multipart({}, ("invoice.pdf", png_bytes()))
    r = await api.post("/v1/documents", content=body, headers={**headers, **o.auth})
    assert r.status_code == 415 and r.json()["code"] == "unsupported_type"
    assert (await _count(sm, o.id))["documents"] == 0


async def test_upload_requires_auth(api: httpx.AsyncClient) -> None:
    body, headers = multipart({}, ("a.pdf", VALID_PDF.read_bytes()))
    assert (await api.post("/v1/documents", content=body, headers=headers)).status_code == 401
