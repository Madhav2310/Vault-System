"""Retrieval-only harness for `eval.run`: no analyze, no generation, no chat-model calls.

    python -m eval.run --suite retrieval --config retrieval@2 --split train \\
        --pipeline eval.pipeline_retrieval:setup

Each item is answered by `Retriever.retrieve` in process against the dev database. `--config` names a
retrieval config file (`backend/config/<name>.yaml`). Only `embed_query` rows reach `llm_calls` (about
$0.00001 each, cached per owner and query). `EVAL_RANK` (rrf, vector, lexical, rerank, final; default final)
picks which candidate ranking is scored, for ablations.

`EVAL_USER` (an email; default `eval-retrieval@vault.local`) names the user whose corpus is scored. Unset, or
naming a user that does not exist yet: the user is created (the default user's API key is rotated each run)
and the documents the items need (`doc_keys`, map: `vault_eval/documents.yaml`) are uploaded through the
running stack's HTTP API once, then reused by sha256. Naming an EXISTING other user: that user's ingested
corpus is scored as is, with no key rotation, no upload and no re-ingestion; every needed document must
already be there (matched by sha256 of the current ready version), otherwise the run exits naming the
missing keys. This is how the corpus an answers run ingested for a split is reused.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import pathlib
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

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
from app.retrieval.retrieve import Retriever
from app.retrieval.scope import compute_scope
from eval.build.build_docs import gold_units, load_documents, missing_files, resolve_file
from eval.mapping import ChunkInfo, chunks_from_rows
from eval.run import Harness, Pipeline
from eval.schema import EvalContext, ItemOutput, Query

EVAL_EMAIL = "eval-retrieval@vault.local"
READY = {"ready", "enriched"}
FAILED = {"failed", "unsupported", "needs_ocr"}


def resolve_config(name: str) -> RetrievalConfig:
    """The named retrieval config, or exit with a message naming the missing file."""
    path = CONFIG_DIR / f"{name}.yaml"
    if not path.is_file():
        raise SystemExit(f"unknown retrieval config {name!r}: {path} does not exist")
    return get_config(name)


@dataclass(frozen=True)
class Cand:
    """One retrieval candidate with the scores and ranks every ranking mode reads."""

    chunk_id: str
    rrf: float | None = None
    rerank: float | None = None
    vector_rank: int | None = None
    lexical_rank: int | None = None
    selected: bool = False
    result_index: int | None = None


def _final_key(c: Cand) -> tuple[int, float, float]:
    return (
        (0, float(c.result_index or 0), 0.0)
        if c.selected
        else (1, -(c.rerank if c.rerank is not None else float("-inf")), -(c.rrf or 0.0))
    )


def rank(cands: Sequence[Cand], mode: str) -> list[str]:
    """rrf: fusion score; vector / lexical: that leg's rank (candidates without one are absent); rerank:
    rerank score (unreranked candidates are absent); final: selected in evidence order, then rerank, rrf."""
    if mode == "rrf":
        return [c.chunk_id for c in sorted(cands, key=lambda c: -(c.rrf or 0.0))]
    if mode == "vector":
        return [
            c.chunk_id for c in sorted((c for c in cands if c.vector_rank), key=lambda c: c.vector_rank or 0)
        ]
    if mode == "lexical":
        return [
            c.chunk_id
            for c in sorted((c for c in cands if c.lexical_rank), key=lambda c: c.lexical_rank or 0)
        ]
    if mode == "rerank":
        return [
            c.chunk_id
            for c in sorted((c for c in cands if c.rerank is not None), key=lambda c: -(c.rerank or 0.0))
        ]
    if mode == "final":
        return [c.chunk_id for c in sorted(cands, key=_final_key)]
    raise ValueError(f"unknown ranking {mode!r}")


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


async def reuse_only(
    sm: async_sessionmaker[AsyncSession], owner_id: uuid.UUID, files: Mapping[str, pathlib.Path]
) -> dict[str, str]:
    """The owner's ingested documents for `files`; exit naming what is missing (nothing is uploaded)."""
    ids = await find_ingested(sm, owner_id, files)
    missing = sorted(set(files) - set(ids))
    if missing:
        raise SystemExit(f"EVAL_USER corpus is incomplete, not re-ingesting; missing documents: {missing}")
    return ids


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
        mode = os.environ.get("EVAL_RANK", "final")
        cands = [
            Cand(
                str(c.chunk_id),
                c.rrf_score,
                c.rerank_score,
                c.vector_rank,
                c.lexical_rank,
                c.selected,
                c.result_index,
            )
            for c in res.candidates
        ]
        return ItemOutput(retrieved=rank(cands, mode), degraded=tuple(res.degraded))

    return pipeline


async def setup(config: str, *, doc_keys: Sequence[str]) -> Harness:
    cfg = resolve_config(config)
    docs = load_documents()
    if bad := missing_files(doc_keys, docs):
        lines = [f"  {k}: {why}" for k, why in list(bad.items())[:20]]
        raise SystemExit(f"{len(bad)} document(s) unavailable:\n" + "\n".join(lines))
    sm = get_sessionmaker()
    settings = get_settings()
    email = os.environ.get("EVAL_USER") or EVAL_EMAIL
    files = {k: resolve_file(docs[k]["file"]) for k in doc_keys}
    async with sm() as s:
        uid = (await s.execute(text("SELECT id FROM users WHERE email = :e"), {"e": email})).scalar()
    if uid is not None and email != EVAL_EMAIL:  # an existing user's corpus: score it as is
        doc_ids = await reuse_only(sm, uid, files)
    else:
        key = new_api_key()
        async with sm() as s:
            if uid is None:
                uid = uuid.uuid4()
                s.add(User(id=uid, email=email, api_key_hash=hash_api_key(key)))
            else:  # the key is not retrievable: rotate it (the default eval user is ours alone)
                await s.execute(
                    text("UPDATE users SET api_key_hash = :h WHERE id = :i"),
                    {"h": hash_api_key(key), "i": uid},
                )
            await s.commit()
        base = os.environ.get("API_BASE_URL", "http://localhost:8100")
        async with httpx.AsyncClient(
            base_url=base, headers={"Authorization": f"Bearer {key}"}, timeout=180
        ) as http:
            doc_ids = await ensure_documents(sm, http, uid, files)
    chunks = await _chunks(sm, uid, doc_ids)
    gw = get_gateway()
    rds = aioredis.from_url(settings.redis_url)
    retriever = Retriever(QueryEmbedder(gw, rds), None, cfg)
    return Harness(
        make_pipeline(sm, retriever, uid, doc_ids),
        chunks,
        EvalContext(owner_id=uid, doc_ids=doc_ids, services={"sm": sm, "retriever": retriever}),
        gold_units=gold_units(docs),
    )
