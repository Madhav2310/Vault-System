"""Real-pipeline harness for `eval.run`: drives the running stack (`make up`) over its HTTP API.

`python -m eval.run --pipeline eval.pipeline_http:setup ...` creates a throwaway eval user, uploads the 15
Vault-Eval documents (deduplicated per owner), waits until ready, and answers each item through
`POST /v1/chat/sessions/{id}/messages`. Retrieved chunk ids come from the run's `retrieval_candidates`
(best first), citations from the stored evidence. Needs DATABASE_URL (host) and API_BASE_URL; the models are
the stack's, so a run costs real money: use the `smoke` suite or `--split` samples.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import os
import pathlib
import time
import uuid
from typing import Any

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


async def setup(config: str, *, with_judges: bool | None = None) -> Harness:
    import os

    base = os.environ.get("API_BASE_URL", "http://localhost:8100")
    ds = load_dataset()
    with_judges = config.startswith("answers") if with_judges is None else with_judges
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
                        "SELECT id, document_id, ordinal, original_text, sentences, page_start, "
                        "page_end, kind "
                        "FROM chunks WHERE owner_id = :o ORDER BY document_id, ordinal"
                    ),
                    {"o": uid},
                )
            )
            .mappings()
            .all()
        )
    chunks: dict[str, list[ChunkInfo]] = {k: [] for k in doc_ids}
    for c in chunks_from_rows([dict(r) for r in rows], by_id):
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
            json={
                "content": q.question,
                "client_message_id": f"{q.item_id}:{q.turn_index}:{uuid.uuid4().hex[:8]}",
            },
        )
        r.raise_for_status()
        ms = (time.monotonic() - t0) * 1000
        msg_id = r.json()["message"]["id"]
        msg = await _find_message(http, sid, msg_id)
        segs = [
            AnswerSegment(
                s["text"],
                tuple(
                    CitedRange(e["chunk_id"], e["sentence_start"], e["sentence_end"]) for e in s["evidence"]
                ),
            )
            for s in msg["segments"]
        ]
        async with sm() as s:
            run = (
                await s.execute(
                    text(
                        "SELECT id, cost_usd FROM rag_runs WHERE assistant_message_id = :m AND owner_id = :o"
                    ),
                    {"m": msg_id, "o": uid},
                )
            ).one()
            call_ids = (
                (await s.execute(text("SELECT id FROM llm_calls WHERE run_id = :r"), {"r": run[0]}))
                .scalars()
                .all()
            )
            cands = (
                (
                    await s.execute(
                        text(
                            "SELECT chunk_id FROM retrieval_candidates WHERE run_id = :r ORDER BY "
                            "rerank_score DESC NULLS LAST, rrf_score DESC NULLS LAST"
                        ),
                        {"r": run[0]},
                    )
                )
                .scalars()
                .all()
            )
        abstained = msg["response_type"] in {"abstain", "clarify"}
        return ItemOutput(
            retrieved=[str(c) for c in cands],
            segments=[] if abstained and not segs else segs,
            abstained=abstained,
            latency_ms=ms,
            cost_usd=float(run[1] or 0),
            call_ids=tuple(str(c) for c in call_ids),
            verdicts=tuple(
                seg["verification"] for seg in msg["segments"] if seg.get("verification") in _VERDICTS
            ),
        )

    get_settings()  # fail early on a bad environment
    started = dt.datetime.now(dt.UTC)

    async def spent() -> float:
        """Answering spend of this eval user since setup, plus the judges' (their own in-memory ledger)."""
        async with sm() as s:
            v = (
                await s.execute(
                    text(
                        "SELECT COALESCE(sum(cost_usd), 0) FROM llm_calls "
                        "WHERE owner_id = :o AND created_at >= :t"
                    ),
                    {"o": uid, "t": started},
                )
            ).scalar_one()
        return float(v) + float(judges.spent if judges else 0)

    judges = _JudgeScorer() if with_judges else None
    return Harness(
        pipeline,
        chunks,
        EvalContext(owner_id=uid, doc_ids=doc_ids),
        new_session,
        extra_scorer=judges.score if judges else None,
        spent=spent,
        close=http.aclose,
    )


_VERDICTS = {"supported", "partial", "unsupported"}


async def _find_message(http: httpx.AsyncClient, sid: uuid.UUID, msg_id: str) -> dict[str, Any]:
    """The assistant message by id: page back through history (never assume it is among the last few)."""
    cursor: str | None = None
    for _ in range(10):
        params: dict[str, Any] = {"limit": 100}
        if cursor:
            params["cursor"] = cursor
        body = (await http.get(f"/v1/chat/sessions/{sid}/messages", params=params)).json()
        for m in body["items"]:
            if m["id"] == msg_id:
                return dict(m)
        cursor = body.get("next_cursor")
        if not cursor:
            break
    raise LookupError(f"message {msg_id} not found in session history")


class _JudgeScorer:
    """Binary faithfulness + completeness judges (deterministic first), capped by their own ledger."""

    def __init__(self) -> None:
        from decimal import Decimal

        from app.llm.gateway import MemoryLedger
        from eval.judge import SpendGuard, _live_gateway

        self.ledger = MemoryLedger()
        self.gw = _live_gateway(self.ledger)
        self.guard = SpendGuard(self.ledger, Decimal(os.environ.get("EVAL_JUDGE_MAX_USD", "1.0")))

    @property
    def spent(self) -> float:
        return float(self.guard.spent)

    async def score(self, item: Any, out: ItemOutput, targets: Any, chunks: Any) -> dict[str, float]:
        from eval.judge import judge_item
        from eval.metrics.answer import judge_metrics, support_metrics

        js = {
            jt: await judge_item(self.gw, item, out, targets, chunks, jt, secondary=False, guard=self.guard)
            for jt in ("faithfulness", "completeness")
        }
        return {**judge_metrics(js), **support_metrics(out)}
