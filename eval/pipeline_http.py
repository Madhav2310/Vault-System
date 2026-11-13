"""Real-pipeline harness for `eval.run`: drives the running stack (`make up`) over its HTTP API.

`python -m eval.run --pipeline eval.pipeline_http:setup ...` creates a throwaway eval user, uploads the 15
Vault-Eval documents (deduplicated per owner), waits until ready, and answers each item through
`POST /v1/chat/sessions/{id}/messages`. Retrieved chunk ids come from the run's `retrieval_candidates`
(best first), citations from the stored evidence. Needs DATABASE_URL (host) and API_BASE_URL; the models are
the stack's, so a run costs real money: use the `smoke` suite or `--split` samples.
"""

from __future__ import annotations

import asyncio
import pathlib
import time
import uuid

import httpx
from sqlalchemy import text

from app.core.auth import hash_api_key, new_api_key
from app.core.settings import get_settings
from app.db.models import User
from app.db.session import get_sessionmaker
from eval.loader import load_dataset
from eval.mapping import ChunkInfo, chunks_from_rows
from eval.run import Harness
from eval.schema import AnswerSegment, CitedRange, EvalContext, ItemOutput, Query

ROOT = pathlib.Path(__file__).resolve().parents[1]
READY = {"ready", "enriched"}


async def setup(config: str) -> Harness:
    import os

    base = os.environ.get("API_BASE_URL", "http://localhost:8100")
    ds = load_dataset()
    sm = get_sessionmaker()
    key, uid = new_api_key(), uuid.uuid4()
    async with sm() as s:
        s.add(User(id=uid, email=f"eval-{uid}@vault.local", api_key_hash=hash_api_key(key)))
        await s.commit()
    http = httpx.AsyncClient(base_url=base, headers={"Authorization": f"Bearer {key}"}, timeout=180)
    doc_ids: dict[str, str] = {}
    for k, meta in ds.documents.items():
        p = ROOT / meta["file"]
        r = await http.post("/v1/documents", files={"file": (p.name, p.read_bytes())})
        r.raise_for_status()
        doc_ids[k] = r.json()["document_id"]
    deadline = time.monotonic() + 900
    for k, did in doc_ids.items():
        while True:
            st = (await http.get(f"/v1/documents/{did}")).json()["status"]
            if st in READY:
                break
            if st in {"failed", "unsupported", "needs_ocr"} or time.monotonic() > deadline:
                raise RuntimeError(f"document {k} ended in {st}")
            await asyncio.sleep(2)
    by_id = {v: k for k, v in doc_ids.items()}
    async with sm() as s:
        rows = (
            (
                await s.execute(
                    text(
                        "SELECT id, document_id, ordinal, original_text, sentences, page_start, page_end, kind "
                        "FROM chunks WHERE owner_id = :o ORDER BY document_id, ordinal"
                    ),
                    {"o": uid},
                )
            )
            .mappings()
            .all()
        )
    chunks: dict[str, list[ChunkInfo]] = {k: [] for k in doc_ids}
    for c in chunks_from_rows(rows, by_id):
        chunks[c.doc_id].append(c)

    async def new_session(q: Query, ctx: EvalContext) -> uuid.UUID:
        body = {"document_ids": [ctx.doc_ids[d] for d in q.scope_docs]} if q.scope_docs else {}
        r = await http.post("/v1/chat/sessions", json=body)
        r.raise_for_status()
        return uuid.UUID(r.json()["id"])

    async def pipeline(q: Query, ctx: EvalContext) -> ItemOutput:
        sid = ctx.session_id or await new_session(q, ctx)
        t0 = time.monotonic()
        r = await http.post(
            f"/v1/chat/sessions/{sid}/messages",
            json={"content": q.question, "client_message_id": f"{q.item_id}:{q.turn_index}:{uuid.uuid4().hex[:8]}"},
        )
        r.raise_for_status()
        ms = (time.monotonic() - t0) * 1000
        msg_id = r.json()["message"]["id"]
        hist = (await http.get(f"/v1/chat/sessions/{sid}/messages", params={"limit": 4})).json()["items"]
        msg = next(m for m in hist if m["id"] == msg_id)
        segs = [
            AnswerSegment(
                s["text"],
                tuple(CitedRange(e["chunk_id"], e["sentence_start"], e["sentence_end"]) for e in s["evidence"]),
            )
            for s in msg["segments"]
        ]
        async with sm() as s:
            run = (
                await s.execute(
                    text("SELECT id, cost_usd FROM rag_runs WHERE assistant_message_id = :m AND owner_id = :o"),
                    {"m": msg_id, "o": uid},
                )
            ).one()
            cands = (
                await s.execute(
                    text(
                        "SELECT chunk_id FROM retrieval_candidates WHERE run_id = :r ORDER BY "
                        "rerank_score DESC NULLS LAST, rrf_score DESC NULLS LAST"
                    ),
                    {"r": run[0]},
                )
            ).scalars().all()
        abstained = msg["response_type"] in {"abstain", "clarify"}
        return ItemOutput(
            retrieved=[str(c) for c in cands],
            segments=[] if abstained and not segs else segs,
            abstained=abstained,
            latency_ms=ms,
            cost_usd=float(run[1] or 0),
        )

    get_settings()  # fail early on a bad environment
    return Harness(pipeline, chunks, EvalContext(owner_id=uid, doc_ids=doc_ids), new_session)
