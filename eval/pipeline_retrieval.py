"""Retrieval-only harness for `eval.run` (plan S11.01): no analyze, no generation, no chat-model calls.

`python -m eval.run --suite retrieval --config retrieval@1 --pipeline eval.pipeline_retrieval:setup`
answers each item by calling `Retriever.retrieve` in process against the dev database, for a stable eval user
(`eval-retrieval@vault.local`). The 15 Vault-Eval documents are uploaded once (through the running stack's
HTTP API) and reused by sha256 afterwards. Only `embed_query` rows reach `llm_calls` (about $0.00001 each).
`--config` names a retrieval config file (`backend/config/<name>.yaml`).
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import pathlib
import time
import uuid
from collections.abc import Mapping

import httpx
import redis.asyncio as aioredis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.auth import hash_api_key, new_api_key
from app.core.settings import get_settings
from app.db.models import User
from app.db.session import get_sessionmaker
from app.llm.gateway import get_gateway
from app.retrieval.config import CONFIG_DIR, RetrievalConfig, get_config
from app.retrieval.query_embed import QueryEmbedder
from app.retrieval.rerank import RerankClient
from app.retrieval.retrieve import Retriever
from app.retrieval.scope import compute_scope
from eval.loader import load_dataset
from eval.mapping import ChunkInfo, chunks_from_rows
from eval.multiturn import Pipeline
from eval.run import Harness
from eval.schema import EvalContext, ItemOutput, Query

ROOT = pathlib.Path(__file__).resolve().parents[1]
EVAL_EMAIL = "eval-retrieval@vault.local"
READY = {"ready", "enriched"}
FAILED = {"failed", "unsupported", "needs_ocr"}


def resolve_config(name: str) -> RetrievalConfig:
    """The named retrieval config, or exit with a message naming the missing file."""
    path = CONFIG_DIR / f"{name}.yaml"
    if not path.is_file():
        raise SystemExit(f"unknown retrieval config {name!r}: {path} does not exist")
    return get_config(name)


def sha256_of(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def find_ingested(
    sm: async_sessionmaker[AsyncSession], owner_id: uuid.UUID, files: Mapping[str, pathlib.Path]
) -> dict[str, str]:
    """Eval document key -> document id of the owner's live document whose current bytes match the file."""
    out: dict[str, str] = {}
    async with sm() as s:
        for key, path in files.items():
            row = (
                await s.execute(
                    text(
                        "SELECT d.id FROM documents d "
                        "JOIN document_versions v ON v.id = d.current_version_id "
                        "WHERE d.owner_id = :o AND d.deleted_at IS NULL AND v.sha256 = :h "
                        "AND v.status IN ('ready', 'enriched') LIMIT 1"
                    ),
                    {"o": owner_id, "h": sha256_of(path)},
                )
            ).first()
            if row:
                out[key] = str(row[0])
    return out


async def ensure_documents(
    sm: async_sessionmaker[AsyncSession],
    http: httpx.AsyncClient,
    owner_id: uuid.UUID,
    files: Mapping[str, pathlib.Path],
    *,
    timeout_s: float = 900,
) -> dict[str, str]:
    """Reuse ingested documents; upload (and wait for) only the missing ones."""
    ids = await find_ingested(sm, owner_id, files)
    missing = {k: p for k, p in files.items() if k not in ids}
    uploaded: dict[str, str] = {}
    for k, p in missing.items():
        r = await http.post("/v1/documents", files={"file": (p.name, p.read_bytes())})
        r.raise_for_status()
        uploaded[k] = r.json()["document_id"]
    deadline = time.monotonic() + timeout_s
    for k, did in uploaded.items():
        while True:
            st = (await http.get(f"/v1/documents/{did}")).json()["status"]
            if st in READY:
                break
            if st in FAILED or time.monotonic() > deadline:
                raise RuntimeError(f"document {k} ended in {st}")
            await asyncio.sleep(2)
    return {**ids, **uploaded}


async def _chunks(
    sm: async_sessionmaker[AsyncSession], owner_id: uuid.UUID, doc_ids: Mapping[str, str]
) -> dict[str, list[ChunkInfo]]:
    by_id = {v: k for k, v in doc_ids.items()}
    async with sm() as s:
        rows = (
            (
                await s.execute(
                    text(
                        "SELECT c.id, c.document_id, c.ordinal, c.original_text, c.sentences, c.page_start, "
                        "c.page_end, c.kind FROM chunks c JOIN documents d ON d.id = c.document_id "
                        "WHERE c.owner_id = :o AND c.version_id = d.current_version_id "
                        "ORDER BY c.document_id, c.ordinal"
                    ),
                    {"o": owner_id},
                )
            )
            .mappings()
            .all()
        )
    chunks: dict[str, list[ChunkInfo]] = {k: [] for k in doc_ids}
    for c in chunks_from_rows([dict(r) for r in rows if str(r["document_id"]) in by_id], by_id):
        chunks[c.doc_id].append(c)
    return chunks


def make_pipeline(
    sm: async_sessionmaker[AsyncSession],
    retriever: Retriever,
    owner_id: uuid.UUID,
    doc_ids: Mapping[str, str],
) -> Pipeline:
    async def pipeline(q: Query, ctx: EvalContext) -> ItemOutput:
        scope_ids = [uuid.UUID(doc_ids[d]) for d in q.scope_docs] if q.scope_docs else None
        async with sm() as s:
            scope = await compute_scope(s, owner_id, scope_ids)
            res = await retriever.retrieve(s, owner_id, scope, q.question)
        selected = sorted((c for c in res.candidates if c.selected), key=lambda c: c.result_index or 0)
        rest = sorted(
            (c for c in res.candidates if not c.selected),
            key=lambda c: (
                -(c.rerank_score if c.rerank_score is not None else float("-inf")),
                -(c.rrf_score or 0),
            ),
        )
        return ItemOutput(retrieved=[str(c.chunk_id) for c in [*selected, *rest]])

    return pipeline


async def setup(config: str) -> Harness:
    cfg = resolve_config(config)
    ds = load_dataset()
    sm = get_sessionmaker()
    settings = get_settings()
    key = new_api_key()
    async with sm() as s:
        uid = (await s.execute(text("SELECT id FROM users WHERE email = :e"), {"e": EVAL_EMAIL})).scalar()
        if uid is None:
            uid = uuid.uuid4()
            s.add(User(id=uid, email=EVAL_EMAIL, api_key_hash=hash_api_key(key)))
        else:  # the key is not retrievable: rotate it (the eval user is ours alone)
            await s.execute(
                text("UPDATE users SET api_key_hash = :h WHERE id = :i"), {"h": hash_api_key(key), "i": uid}
            )
        await s.commit()
    files = {k: ROOT / m["file"] for k, m in ds.documents.items()}
    base = os.environ.get("API_BASE_URL", "http://localhost:8100")
    async with httpx.AsyncClient(
        base_url=base, headers={"Authorization": f"Bearer {key}"}, timeout=180
    ) as http:
        doc_ids = await ensure_documents(sm, http, uid, files)
    chunks = await _chunks(sm, uid, doc_ids)
    gw = get_gateway()
    rds = aioredis.from_url(settings.redis_url)
    retriever = Retriever(QueryEmbedder(gw, rds), RerankClient.from_settings(settings), None, cfg)
    return Harness(
        make_pipeline(sm, retriever, uid, doc_ids), chunks, EvalContext(owner_id=uid, doc_ids=doc_ids)
    )
