"""Helpers for upload tests: tiny valid files of each type and a streaming multipart body builder."""

import io
import pathlib
from collections.abc import AsyncIterator

import docx

FIXTURES = pathlib.Path(__file__).resolve().parents[1] / "fixtures"
VALID_PDF = FIXTURES / "geometry" / "ligatures.pdf"
BOUNDARY = "vaulttestboundary7MA4YWxkTrZu0gW"


def docx_bytes(text: str = "Hello from a Word document.") -> bytes:
    d = docx.Document()
    d.add_paragraph(text)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def png_bytes() -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def multipart(fields: dict[str, str], file: tuple[str, bytes] | None) -> tuple[bytes, dict[str, str]]:
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(
            f'--{BOUNDARY}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        )
    if file is not None:
        fname, data = file
        parts.append(
            f'--{BOUNDARY}\r\nContent-Disposition: form-data; name="file"; filename="{fname}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n".encode()
            + data
            + b"\r\n"
        )
    parts.append(f"--{BOUNDARY}--\r\n".encode())
    return b"".join(parts), {"Content-Type": f"multipart/form-data; boundary={BOUNDARY}"}


async def streamed_file_body(filename: str, size: int, chunk: int = 64 * 1024) -> AsyncIterator[bytes]:
    """Multipart body whose file part is `size` bytes of PDF-looking data, yielded chunk by chunk."""
    yield (
        f'--{BOUNDARY}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        "Content-Type: application/pdf\r\n\r\n%PDF-1.7\n"
    ).encode()
    left = size - len(b"%PDF-1.7\n")
    block = b"0" * chunk
    while left > 0:
        n = min(chunk, left)
        yield block[:n]
        left -= n
    yield f"\r\n--{BOUNDARY}--\r\n".encode()


STREAM_HEADERS = {"Content-Type": f"multipart/form-data; boundary={BOUNDARY}"}
