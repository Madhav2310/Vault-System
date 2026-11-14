"""Object store contract.

Keys are content-addressed or version-scoped, never user-chosen.
"""

import pathlib
import re
from typing import Protocol

_KEY = re.compile(r"^(?!/)(?!.*(^|/)\.\.?(/|$))[A-Za-z0-9._/-]{1,512}$")


class InvalidKeyError(ValueError):
    pass


def validate_key(key: str) -> str:
    if not _KEY.match(key) or "//" in key:
        raise InvalidKeyError("invalid object key")
    return key


class ObjectStore(Protocol):
    async def put(self, key: str, data: bytes) -> None: ...
    async def put_file(self, key: str, path: pathlib.Path) -> None:
        """Store a local file's bytes without loading them into memory."""
        ...

    async def get(self, key: str) -> bytes: ...
    async def delete(self, key: str) -> None: ...
    async def exists(self, key: str) -> bool: ...
    def url(self, key: str) -> str: ...
