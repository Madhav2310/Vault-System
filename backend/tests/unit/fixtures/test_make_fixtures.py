import hashlib
import io
import zipfile
from pathlib import Path

import pdfplumber
import pypdf
import pytest

from tests.fixtures import make_fixtures as mf

MB = 1024 * 1024


def _hashes(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _generate(root: Path, *, include_oversize: bool = True) -> None:
    mf.generate_upload_fixtures(root / "files", include_oversize=include_oversize)
    mf.generate_geometry_zoo(root / "geometry")


def test_deterministic(tmp_path: Path) -> None:
    _generate(tmp_path / "a")
    _generate(tmp_path / "b")
    a, b = _hashes(tmp_path / "a"), _hashes(tmp_path / "b")
    assert len(a) == len(mf.UPLOAD_FIXTURES) + 2 * len(mf.GEOMETRY_CASES)
    assert a == b


def _zip_members(data: bytes) -> list[tuple[str, int, int]]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return [(i.filename, i.CRC, i.file_size) for i in zf.infolist()]


def test_committed_fixtures_current(tmp_path: Path) -> None:
    """Committed fixtures equal the generator's output (zip members compared by CRC: deflate
    bytes can differ across zlib builds; the oversize file is never committed)."""
    _generate(tmp_path, include_oversize=False)
    for sub in ("files", "geometry"):
        fresh = sorted(p.name for p in (tmp_path / sub).iterdir())
        committed = sorted(p.name for p in (mf.HERE / sub).iterdir() if p.name != "oversize.pdf")
        assert committed == fresh, sub
        for name in fresh:
            new, old = (tmp_path / sub / name).read_bytes(), (mf.HERE / sub / name).read_bytes()
            if name.endswith(".docx"):
                assert _zip_members(new) == _zip_members(old), name
            else:
                assert new == old, f"{sub}/{name} is stale: run python -m tests.fixtures.make_fixtures"


def test_all_types_present(tmp_path: Path) -> None:
    paths = mf.generate_upload_fixtures(tmp_path)
    assert set(paths) == {
        "spoofed_type", "oversize", "encrypted", "corrupt", "zip_bomb", "empty_text", "prompt_injection"
    }  # fmt: skip
    assert all(p.is_file() for p in paths.values())

    spoofed = paths["spoofed_type"]
    assert spoofed.suffix == ".pdf"
    assert spoofed.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")

    oversize = paths["oversize"]
    assert oversize.stat().st_size > 25 * MB
    with pdfplumber.open(oversize) as pdf:  # a valid PDF, rejected only for its size
        assert len(pdf.pages) == 1

    reader = pypdf.PdfReader(paths["encrypted"])
    assert reader.is_encrypted
    assert not reader.decrypt("")  # no empty user password

    corrupt = paths["corrupt"].read_bytes()
    assert corrupt.startswith(b"%PDF-") and b"%%EOF" not in corrupt
    with pytest.raises(pypdf.errors.PdfReadError):
        pypdf.PdfReader(paths["corrupt"], strict=True)

    bomb = paths["zip_bomb"]
    with zipfile.ZipFile(bomb) as zf:
        assert "word/document.xml" in zf.namelist()
        declared = sum(i.file_size for i in zf.infolist())
    assert bomb.stat().st_size < 1 * MB
    assert declared >= 100 * MB
    assert declared / bomb.stat().st_size > 500

    with pdfplumber.open(paths["empty_text"]) as pdf:
        page = pdf.pages[0]
        assert page.chars == []
        assert len(page.images) == 1

    with pdfplumber.open(paths["prompt_injection"]) as pdf:
        text = pdf.pages[0].extract_text()
    assert "Ignore all previous instructions" in text
    assert "Receipts are required" in text
