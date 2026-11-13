"""Ingest Vault-Eval documents through the real stages (FakeProvider embeddings: no API cost) and read their
chunks back as `ChunkInfo`, for gold-mapping and harness tests."""

import os
import pathlib
import uuid
from typing import Any

import httpx
from eval.mapping import ChunkInfo, chunks_from_rows
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.settings import get_settings
from app.ingestion.runner import run_job
from app.ingestion.stages import default_stages
from tests.support.embed import db_gateway
from tests.support.evalkit import REPO_ROOT
from tests.support.jobs import version_status
from tests.support.pipeline import upload


def source_path(meta: dict[str, Any]) -> pathlib.Path | None:
    """documents.yaml `file`, looked up under this checkout, then under $VAULT_CORPUS_ROOT (a checkout with
    the gitignored eval/sources/, e.g. when running from a worktree)."""
    for root in (REPO_ROOT, pathlib.Path(os.environ.get("VAULT_CORPUS_ROOT", REPO_ROOT))):
        p = root / meta["file"]
        if p.is_file():
            return p
    return None


async def ingest(
    api: httpx.AsyncClient,
    owner: Any,
    sm: async_sessionmaker[AsyncSession],
    store: Any,
    enqueued: Any,
    tmp_path: pathlib.Path,
    docs: dict[str, dict[str, Any]],
) -> tuple[dict[str, list[ChunkInfo]], dict[str, str]]:
    """Upload and fully process each document in `docs` (key -> documents.yaml entry with a file on disk).
    Returns (chunks by document key, ingested document id by key)."""
    chunks: dict[str, list[ChunkInfo]] = {}
    doc_ids: dict[str, str] = {}
    gw, _ = db_gateway(tmp_path, sm)
    services = {"store": store, "settings": get_settings(), "gateway": gw}
    for key, meta in docs.items():
        path = source_path(meta)
        assert path is not None, key
        up = await upload(api, owner, path.name, path.read_bytes())
        for _ in range(8):
            r = await run_job(sm, uuid.UUID(up["job_id"]), "w", default_stages(), enqueued, services=services)
            if r != "advanced":
                break
        status, err = await version_status(sm, uuid.UUID(up["version_id"]))
        assert (status, err) == ("ready", None), (key, status, err)
        async with sm() as s:
            rows = (
                (
                    await s.execute(
                        text(
                            "SELECT id, document_id, ordinal, original_text, sentences, page_start, "
                            "page_end, kind "
                            "FROM chunks WHERE version_id = :v AND owner_id = :o ORDER BY ordinal"
                        ),
                        {"v": up["version_id"], "o": owner.id},
                    )
                )
                .mappings()
                .all()
            )
        doc_ids[key] = up["document_id"]
        chunks[key] = chunks_from_rows(rows, {up["document_id"]: key})
    return chunks, doc_ids
