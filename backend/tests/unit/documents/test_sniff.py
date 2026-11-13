import pathlib
import zipfile

import pytest

from app.api.errors import DomainError
from app.documents.sniff import DOCX, MD, PDF, TXT, sniff
from tests.support.uploads import VALID_PDF, docx_bytes


def _write(tmp: pathlib.Path, name: str, data: bytes) -> pathlib.Path:
    p = tmp / name
    p.write_bytes(data)
    return p


def test_allowed_types(tmp_path: pathlib.Path) -> None:
    assert sniff(_write(tmp_path, "a.bin", VALID_PDF.read_bytes()), "report.bin") == PDF
    assert sniff(_write(tmp_path, "b.bin", docx_bytes()), "contract.pdf") == DOCX  # extension ignored
    assert sniff(_write(tmp_path, "c.txt", "Plain text, naïve café.\n".encode()), "notes.txt") == TXT
    assert sniff(_write(tmp_path, "d.md", b"# Title\n\nBody\n"), "README.md") == MD
    assert sniff(_write(tmp_path, "e", b"# Title\n"), "no_extension") == TXT


def test_plain_zip_rejected(tmp_path: pathlib.Path) -> None:
    p = tmp_path / "x.docx"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("hello.txt", "not a word document")
    with pytest.raises(DomainError) as e:
        sniff(p, "x.docx")
    assert e.value.code.value == "unsupported_type"


def test_binary_txt_rejected(tmp_path: pathlib.Path) -> None:
    for data in (b"abc\x00def", b"\xff\xfe\xfa invalid utf-8"):
        with pytest.raises(DomainError) as e:
            sniff(_write(tmp_path, "bin.txt", data), "bin.txt")
        assert e.value.code.value == "unsupported_type"
