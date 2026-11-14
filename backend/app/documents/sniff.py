"""Magic-byte type detection. The client's extension and Content-Type are never trusted.

Allowed: PDF, DOCX (a ZIP with `[Content_Types].xml` and a `word/` part), and UTF-8 text without NUL bytes.
Text is `text/markdown` when the filename says so, else `text/plain`: bytes cannot tell the two apart,
and both go through the same extractor.
"""

import codecs
import pathlib
import zipfile

from app.api.errors import DomainError
from app.core.error_codes import ErrorCode

PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
TXT = "text/plain"
MD = "text/markdown"
ALLOWED = frozenset({PDF, DOCX, TXT, MD})
_MD_SUFFIXES = {".md", ".markdown"}
_READ = 64 * 1024


def _unsupported(what: str) -> DomainError:
    return DomainError(ErrorCode.UNSUPPORTED_TYPE, f"{what}; allowed: PDF, DOCX, TXT, MD")


def _is_docx(path: pathlib.Path) -> bool:
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()  # reads the central directory only; nothing is decompressed
    except (zipfile.BadZipFile, OSError):
        return False
    return "[Content_Types].xml" in names and any(n.startswith("word/") for n in names)


def _is_utf8_text(path: pathlib.Path) -> bool:
    dec = codecs.getincrementaldecoder("utf-8")()
    with path.open("rb") as f:
        while chunk := f.read(_READ):
            if b"\x00" in chunk:
                return False
            try:
                dec.decode(chunk)
            except UnicodeDecodeError:
                return False
    try:
        dec.decode(b"", final=True)
    except UnicodeDecodeError:
        return False
    return True


def sniff(path: pathlib.Path, filename: str) -> str:
    """Return the detected MIME type or raise 415 `unsupported_type`."""
    with path.open("rb") as f:
        head = f.read(8)
    if head.startswith(b"%PDF-"):
        return PDF
    if head.startswith(b"PK\x03\x04"):
        if _is_docx(path):
            return DOCX
        raise _unsupported("ZIP archive that is not a Word document")
    if _is_utf8_text(path):
        return MD if pathlib.PurePath(filename).suffix.lower() in _MD_SUFFIXES else TXT
    raise _unsupported("binary content of an unsupported type")
