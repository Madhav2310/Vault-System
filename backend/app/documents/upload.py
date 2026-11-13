"""Streaming multipart upload (design §6.1).

The body is parsed incrementally: the `file` part streams into a temp file in the server's chunk size
(~64 KB) while sha256 is computed, and the upload aborts with 413 as soon as the file exceeds the cap.
Nothing larger than one chunk is held in memory. The temp file is removed when the context exits,
on success and on failure.
"""

import asyncio
import contextlib
import hashlib
import os
import pathlib
import tempfile
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import IO

from fastapi import Request
from python_multipart import MultipartParser
from python_multipart.exceptions import MultipartParseError
from python_multipart.multipart import parse_options_header

from app.api.errors import DomainError
from app.core.error_codes import ErrorCode

MAX_TITLE_BYTES = 1024
MULTIPART_OVERHEAD = 64 * 1024  # headers and boundaries allowed on top of the file cap


@dataclass(frozen=True)
class ReceivedFile:
    path: pathlib.Path
    sha256: str
    size: int
    filename: str
    title: str | None


def _clean(s: str) -> str:
    """Drop control characters (NUL makes Postgres reject the row) and trim."""
    return "".join(ch for ch in s if ch >= " " and ch != "\x7f").strip()


def _too_large(max_bytes: int) -> DomainError:
    return DomainError(ErrorCode.FILE_TOO_LARGE, f"files are limited to {max_bytes} bytes")


def _invalid(detail: str) -> DomainError:
    return DomainError(ErrorCode.VALIDATION_ERROR, detail)


class _Receiver:
    """python-multipart callbacks: route the `file` part to disk, buffer the small `title` part."""

    def __init__(self, tmp: IO[bytes], max_bytes: int):
        self.tmp, self.max_bytes = tmp, max_bytes
        self.hasher = hashlib.sha256()
        self.size = 0
        self.filename: str | None = None
        self.title = bytearray()
        self.files_seen = 0
        self._field = bytearray()
        self._value = bytearray()
        self._headers: dict[str, str] = {}
        self._part: str | None = None  # "file" | "title" | None (ignored part)

    def on_header_field(self, data: bytes, start: int, end: int) -> None:
        self._field += data[start:end]

    def on_header_value(self, data: bytes, start: int, end: int) -> None:
        self._value += data[start:end]

    def on_header_end(self) -> None:
        self._headers[self._field.decode("latin-1").lower()] = self._value.decode("utf-8", "replace")
        self._field.clear()
        self._value.clear()

    def on_headers_finished(self) -> None:
        _, opts = parse_options_header(self._headers.get("content-disposition", ""))
        name = opts.get(b"name", b"").decode("utf-8", "replace")
        if name == "file":
            self.files_seen += 1
            if self.files_seen > 1:
                raise _invalid("exactly one `file` part is allowed")
            raw = opts.get(b"filename", b"").decode("utf-8", "replace")
            self.filename = _clean(pathlib.PurePath(raw.replace("\\", "/")).name) or "upload"
            self._part = "file"
        elif name == "title":
            self._part = "title"
        else:
            self._part = None
        self._headers.clear()

    def on_part_data(self, data: bytes, start: int, end: int) -> None:
        chunk = data[start:end]
        if self._part == "file":
            self.size += len(chunk)
            if self.size > self.max_bytes:
                raise _too_large(self.max_bytes)
            self.hasher.update(chunk)
            self.tmp.write(chunk)
        elif self._part == "title":
            self.title += chunk
            if len(self.title) > MAX_TITLE_BYTES:
                raise _invalid(f"title is limited to {MAX_TITLE_BYTES} bytes")

    def on_part_end(self) -> None:
        self._part = None


@contextlib.asynccontextmanager
async def receive_upload(
    request: Request, max_bytes: int, tmp_dir: str | None = None
) -> AsyncIterator[ReceivedFile]:
    ctype, opts = parse_options_header(request.headers.get("content-type", ""))
    boundary = opts.get(b"boundary")
    if ctype != b"multipart/form-data" or not boundary:
        raise _invalid("expected a multipart/form-data body with a `file` part")
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > max_bytes + MULTIPART_OVERHEAD:
        raise _too_large(max_bytes)  # reject before reading anything

    fd, name = tempfile.mkstemp(prefix="upload-", dir=tmp_dir)
    path = pathlib.Path(name)
    try:
        with os.fdopen(fd, "wb") as tmp:
            rx = _Receiver(tmp, max_bytes)
            parser = MultipartParser(
                boundary,
                {
                    "on_header_field": rx.on_header_field,
                    "on_header_value": rx.on_header_value,
                    "on_header_end": rx.on_header_end,
                    "on_headers_finished": rx.on_headers_finished,
                    "on_part_data": rx.on_part_data,
                    "on_part_end": rx.on_part_end,
                },
            )
            total = 0
            async for chunk in request.stream():
                total += len(chunk)
                if total > max_bytes + MULTIPART_OVERHEAD:  # also when Content-Length is absent (chunked)
                    raise _too_large(max_bytes)
                try:
                    parser.write(chunk)
                except MultipartParseError:
                    raise _invalid("malformed multipart body") from None
            try:
                parser.finalize()
            except MultipartParseError:
                raise _invalid("malformed multipart body") from None
        if rx.filename is None:
            raise _invalid("missing `file` part")
        if rx.size == 0:
            raise _invalid("the file is empty")
        title = _clean(rx.title.decode("utf-8", "replace")) or None
        yield ReceivedFile(path, rx.hasher.hexdigest(), rx.size, rx.filename, title)
    finally:
        await asyncio.to_thread(path.unlink, missing_ok=True)
