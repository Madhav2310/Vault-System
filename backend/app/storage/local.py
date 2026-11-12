"""Local filesystem object store.

Writes are atomic (temp file + fsync + rename); file IO runs off the event loop.
"""

import asyncio
import contextlib
import hashlib
import os
import pathlib
import tempfile

from app.storage.base import InvalidKeyError, validate_key


class LocalObjectStore:
    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = pathlib.Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> pathlib.Path:
        p = (self.root / validate_key(key)).resolve()
        if not p.is_relative_to(self.root):
            raise InvalidKeyError("invalid object key")
        return p

    async def put(self, key: str, data: bytes) -> None:
        await asyncio.to_thread(self._put_sync, self._path(key), data)

    @staticmethod
    def _put_sync(path: pathlib.Path, data: bytes) -> None:
        if (
            path.exists()
            and path.stat().st_size == len(data)
            and hashlib.sha256(path.read_bytes()).digest() == hashlib.sha256(data).digest()
        ):
            return  # idempotent: identical bytes already stored
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except BaseException:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp)
            raise

    async def get(self, key: str) -> bytes:
        return await asyncio.to_thread(self._path(key).read_bytes)

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._path(key).unlink, missing_ok=True)

    async def exists(self, key: str) -> bool:
        return await asyncio.to_thread(self._path(key).is_file)

    def url(self, key: str) -> str:
        return self._path(key).as_uri()
