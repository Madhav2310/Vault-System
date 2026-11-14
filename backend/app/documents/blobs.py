"""Content-addressed blobs.

The file goes to `blobs/{sha256}` before the database transaction: the write is idempotent (same key,
same bytes), so a crash can at worst leave an unreferenced file, never a row pointing to a missing one.
`blobs.refcount` counts referencing versions and is incremented inside the version's transaction.
"""

import pathlib

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.storage.base import ObjectStore


def blob_key(sha256: str) -> str:
    return f"blobs/{sha256}"


async def store_blob(store: ObjectStore, path: pathlib.Path, sha256: str) -> str:
    key = blob_key(sha256)
    await store.put_file(key, path)
    return key


async def add_blob_ref(session: AsyncSession, sha256: str, size: int, mime: str) -> None:
    """Upsert the blob row and count one more reference. Concurrent-safe (single statement)."""
    await session.execute(
        text(
            "INSERT INTO blobs (sha256, size_bytes, mime, storage_key, refcount) "
            "VALUES (:sha, :size, :mime, :key, 1) "
            "ON CONFLICT (sha256) DO UPDATE SET refcount = blobs.refcount + 1"
        ),
        {"sha": sha256, "size": size, "mime": mime, "key": blob_key(sha256)},
    )
