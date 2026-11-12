import os
import pathlib

import pytest

from app.storage.base import InvalidKeyError
from app.storage.local import LocalObjectStore


@pytest.fixture
def store(tmp_path: pathlib.Path) -> LocalObjectStore:
    return LocalObjectStore(tmp_path / "blobs")


async def test_roundtrip(store: LocalObjectStore) -> None:
    await store.put("blobs/ab/abcdef", b"\x00hello\xff")
    assert await store.exists("blobs/ab/abcdef")
    assert await store.get("blobs/ab/abcdef") == b"\x00hello\xff"
    await store.delete("blobs/ab/abcdef")
    assert not await store.exists("blobs/ab/abcdef")


async def test_put_idempotent(store: LocalObjectStore) -> None:
    await store.put("k/1", b"same bytes")
    path = store.root / "k/1"
    os.utime(path, (1_000_000, 1_000_000))
    await store.put("k/1", b"same bytes")
    assert path.stat().st_mtime == 1_000_000
    await store.put("k/1", b"other bytes")  # different content still replaces
    assert await store.get("k/1") == b"other bytes"


async def test_atomic_write(store: LocalObjectStore, monkeypatch: pytest.MonkeyPatch) -> None:
    await store.put("k/obj", b"old")

    def crash(*_: object) -> None:
        raise OSError("simulated crash before rename")

    monkeypatch.setattr(os, "replace", crash)
    with pytest.raises(OSError, match="simulated crash"):
        await store.put("k/obj", b"new partially written content")
    monkeypatch.undo()
    assert await store.get("k/obj") == b"old"
    assert [p.name for p in (store.root / "k").iterdir()] == ["obj"]  # no temp leftovers


@pytest.mark.parametrize(
    "key", ["../etc/passwd", "/etc/passwd", "a/../../b", "a//b", "a/./b", "", "a\\b", "..", "a/b/.."]
)
async def test_path_traversal_rejected(store: LocalObjectStore, key: str) -> None:
    with pytest.raises(InvalidKeyError):
        await store.put(key, b"x")
