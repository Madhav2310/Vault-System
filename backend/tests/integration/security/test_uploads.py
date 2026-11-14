"""S21.09: hostile uploads. Oversized, forged (type and name), and archive bombs are refused or contained
without trusting anything the client declares."""

import io
import pathlib
import zipfile
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ingestion.extract.docx import MAX_RATIO, MAX_UNCOMPRESSED_BYTES, UnsafeArchiveError, check_archive
from tests.support.uploads import (
    STREAM_HEADERS,
    VALID_PDF,
    docx_bytes,
    multipart,
    png_bytes,
    streamed_file_body,
)

MB = 1024 * 1024


async def _post(
    api: httpx.AsyncClient, owner: Any, name: str, data: bytes, ctype: str | None = None
) -> httpx.Response:
    body, h = multipart({}, (name, data))
    if ctype is not None:
        body = body.replace(b"application/octet-stream", ctype.encode())
    return await api.post("/v1/documents", content=body, headers={**h, **owner.auth})


async def _doc_count(sm: async_sessionmaker[AsyncSession], owner_id: Any) -> int:
    async with sm() as s:
        n = (
            await s.execute(text("SELECT count(*) FROM documents WHERE owner_id = :o"), {"o": owner_id})
        ).scalar_one()
    return int(n)


async def test_oversized_upload_refused_and_nothing_stored(
    api: httpx.AsyncClient, make_owner: Callable[[], Any], sm: async_sessionmaker[AsyncSession]
) -> None:
    o = await make_owner()
    r = await api.post(
        "/v1/documents", content=streamed_file_body("big.pdf", 26 * MB), headers={**STREAM_HEADERS, **o.auth}
    )
    assert r.status_code == 413 and r.json()["code"] == "file_too_large"
    assert await _doc_count(sm, o.id) == 0


async def test_declared_content_length_over_limit_refused_early(
    api: httpx.AsyncClient, make_owner: Callable[[], Any]
) -> None:
    o = await make_owner()
    body, h = multipart({}, ("a.pdf", VALID_PDF.read_bytes()))
    r = await api.post("/v1/documents", content=body, headers={**h, **o.auth, "Content-Length": str(30 * MB)})
    assert r.status_code in (400, 413, 422)  # a lying length is refused, never read to the end


@pytest.mark.parametrize(
    ("name", "data", "ctype"),
    [
        ("evil.pdf", png_bytes(), "application/pdf"),  # image bytes, PDF name and type
        ("evil.pdf", b"MZ\x90\x00" + b"\x00" * 100, "application/pdf"),  # executable
        ("evil.docx", b"not a zip at all", None),  # DOCX name, plain text content: allowed as text, see below
        ("evil.docx", b"PK\x03\x04" + b"\x00" * 64, None),  # truncated zip magic
        ("evil.txt", b"abc\x00def", "text/plain"),  # NUL in "text"
        ("evil.txt", b"\xff\xfe\xfa binary", "text/plain"),  # not UTF-8
    ],
)
async def test_forged_content_rejected(
    api: httpx.AsyncClient,
    make_owner: Callable[[], Any],
    sm: async_sessionmaker[AsyncSession],
    name: str,
    data: bytes,
    ctype: str | None,
) -> None:
    o = await make_owner()
    r = await _post(api, o, name, data, ctype)
    if data == b"not a zip at all":
        # valid UTF-8 text is a legal TXT whatever the name says: it must be treated as text, never as DOCX
        assert r.status_code == 202
        async with sm() as s:
            mime = (
                await s.execute(text("SELECT mime FROM document_versions WHERE owner_id = :o"), {"o": o.id})
            ).scalar_one()
        assert mime == "text/plain"
        return
    assert r.status_code == 415 and r.json()["code"] == "unsupported_type", r.text
    assert await _doc_count(sm, o.id) == 0


async def test_zip_that_is_not_docx_rejected(
    api: httpx.AsyncClient, make_owner: Callable[[], Any], sm: async_sessionmaker[AsyncSession]
) -> None:
    o = await make_owner()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("payload.exe", b"MZ")
        z.writestr("../../escape.txt", b"zip-slip")
    r = await _post(api, o, "report.docx", buf.getvalue())
    assert r.status_code == 415 and await _doc_count(sm, o.id) == 0


async def test_filename_is_reduced_to_a_base_name(
    api: httpx.AsyncClient, make_owner: Callable[[], Any], sm: async_sessionmaker[AsyncSession]
) -> None:
    o = await make_owner()
    names = [
        "../../etc/passwd.pdf",
        "..\\..\\windows\\system32\\x.pdf",
        "/abs/path/y.pdf",
        'a"\r\nX-Evil: 1.pdf',
    ]
    for i, n in enumerate(names):  # distinct bytes: identical content would dedupe to the first filename
        r = await _post(api, o, n, VALID_PDF.read_bytes() + f"\n%{i}".encode())
        assert r.status_code in (200, 202), (n, r.text)
    async with sm() as s:
        stored = (
            (await s.execute(text("SELECT filename FROM document_versions WHERE owner_id = :o"), {"o": o.id}))
            .scalars()
            .all()
        )
    assert len(stored) == len(names)
    for f in stored:
        assert "/" not in f and "\\" not in f and ".." not in f and "\r" not in f and "\n" not in f


async def test_docx_bomb_is_refused_by_the_extractor(tmp_path: pathlib.Path) -> None:
    """Upload only reads the central directory (cheap); the extractor refuses on declared size and ratio
    before python-docx or `zipfile.read` touches any member."""
    over_total = tmp_path / "total.docx"
    with zipfile.ZipFile(over_total, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "<x/>")
        zi = zipfile.ZipInfo("word/document.xml")
        zi.compress_type = zipfile.ZIP_DEFLATED
        with z.open(zi, "w") as f:
            block = b"\x00" * MB
            for _ in range(MAX_UNCOMPRESSED_BYTES // MB + 5):
                f.write(block)
    assert over_total.stat().st_size < 2 * MB
    with pytest.raises(UnsafeArchiveError):
        check_archive(str(over_total))

    ratio = tmp_path / "ratio.docx"
    with zipfile.ZipFile(ratio, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "<x/>")
        z.writestr("word/document.xml", b"A" * (MB * 2))
    assert ratio.stat().st_size * MAX_RATIO < 2 * MB
    with pytest.raises(UnsafeArchiveError):
        check_archive(str(ratio))


async def test_bomb_upload_is_accepted_cheaply_and_never_decompressed_by_the_api(
    api: httpx.AsyncClient, make_owner: Callable[[], Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The API must not unzip members while sniffing: a bomb upload returns 202 (fails later, in the worker's
    extract stage, as `unsafe_archive`)."""
    o = await make_owner()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "<x/>")
        z.writestr("word/document.xml", b"\x00" * (50 * MB))

    def boom(*_a: object, **_k: object) -> bytes:
        raise AssertionError("the API decompressed a zip member")

    monkeypatch.setattr(zipfile.ZipFile, "read", boom)
    monkeypatch.setattr(zipfile.ZipFile, "extractall", boom)
    r = await _post(api, o, "bomb.docx", buf.getvalue())
    assert r.status_code == 202, r.text


async def test_valid_docx_still_accepted(api: httpx.AsyncClient, make_owner: Callable[[], Any]) -> None:
    o = await make_owner()
    assert (await _post(api, o, "ok.docx", docx_bytes())).status_code == 202


@pytest.mark.parametrize("name", ["报告_Отчёт_😀.pdf", "résumé.pdf", "ß\u202e.pdf"])
async def test_non_latin1_filename_regression(
    api: httpx.AsyncClient, make_owner: Callable[[], Any], sm: async_sessionmaker[AsyncSession], name: str
) -> None:
    """Regression (S21.09 finding): the multipart header value was UTF-8-decoded and then re-encoded as
    latin-1 by python-multipart: a filename outside latin-1 was a 500, accented ones became U+FFFD."""
    o = await make_owner()
    r = await _post(api, o, name, VALID_PDF.read_bytes())
    assert r.status_code == 202, r.text
    async with sm() as s:
        stored = (
            await s.execute(text("SELECT filename FROM document_versions WHERE owner_id = :o"), {"o": o.id})
        ).scalar_one()
    assert "\ufffd" not in stored and stored.replace("\u202e", "") == name.replace("\u202e", "")
