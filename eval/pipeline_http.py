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
from collections.abc import Sequence
from typing import Any

import httpx
from sqlalchemy import text

from app.core.auth import hash_api_key, new_api_key
from app.core.settings import get_settings
from app.db.models import User
from app.db.session import get_sessionmaker
from eval.fingerprint import merge_answer_config
from eval.loader import load_dataset
from eval.mapping import ChunkInfo, chunks_from_rows
from eval.run import Harness
from eval.schema import AnswerSegment, CitedRange, EvalContext, ItemOutput, Query
from eval.sse import read_turn_stream

ROOT = pathlib.Path(__file__).resolve().parents[1]
READY = {"ready", "enriched"}


def _load_reuse(path: str, keys: list[str]) -> tuple[str, uuid.UUID, dict[str, str]] | None:
    import json as _json

    p = pathlib.Path(path)
    if not p.exists():
        return None
    d = _json.loads(p.read_text())
    if not set(keys) <= set(d["doc_ids"]):
        return None
    return d["key"], uuid.UUID(d["uid"]), {k: d["doc_ids"][k] for k in keys}


def _save_reuse(path: str, key: str, uid: uuid.UUID, doc_ids: dict[str, str]) -> None:
    import json as _json

    p = pathlib.Path(path)
    prev = _json.loads(p.read_text())["doc_ids"] if p.exists() else {}
    p.write_text(_json.dumps({"key": key, "uid": str(uid), "doc_ids": {**prev, **doc_ids}}))
    p.chmod(0o600)


async def _all_ready(http: httpx.AsyncClient, doc_ids: dict[str, str]) -> bool:
    for did in doc_ids.values():
        r = await http.get(f"/v1/documents/{did}")
        if r.status_code != 200 or r.json()["status"] not in READY:
            return False
    return True


async def setup(
    config: str,
    *,
    with_judges: bool | None = None,
    stream: bool = False,
    dataset: str = "v1",
    doc_keys: Sequence[str] | None = None,
) -> Harness:
    """`dataset="v2"` uploads the documents `doc_keys` names (map: vault_eval_v2/documents.yaml) in
    ingest-light mode (wait for `ready`; the brief stage is never awaited), attaches judges, gold."""
    import os

    base = os.environ.get("API_BASE_URL", "http://localhost:8100")
    v2 = dataset == "v2"
    if v2:
        from eval.build.build_v2_docs import document_units, load_documents_v2, missing_files, resolve_file

        v2_docs = load_documents_v2()
        keys = list(doc_keys or ())
        if bad := missing_files(keys, v2_docs):
            lines = [f"  {k}: {why}" for k, why in list(bad.items())[:20]]
            raise RuntimeError(f"{len(bad)} v2 document(s) unavailable:\n" + "\n".join(lines))
        documents: dict[str, dict[str, Any]] = {k: v2_docs[k] for k in keys}
    else:
        documents = load_dataset().documents
    with_judges = config.startswith("answers") if with_judges is None else with_judges
    sm = get_sessionmaker()
    reuse_path = os.environ.get(
        "EVAL_REUSE_CACHE"
    )  # a gitignored json: keep one ingested corpus across A/B arms
    doc_ids: dict[str, str] = {}
    reused = _load_reuse(reuse_path, list(documents)) if reuse_path and v2 else None
    if reused is not None:
        key, uid, doc_ids = reused
        http = httpx.AsyncClient(base_url=base, headers={"Authorization": f"Bearer {key}"}, timeout=180)
        if not await _all_ready(http, doc_ids):
            reused = None
            await http.aclose()
    if reused is None:
        key, uid = new_api_key(), uuid.uuid4()
        async with sm() as s:
            s.add(  # admin: exempt from the daily cost quota, which a benchmark run would exhaust
                User(id=uid, email=f"eval-{uid}@vault.local", api_key_hash=hash_api_key(key), is_admin=True)
            )
            await s.commit()
        http = httpx.AsyncClient(base_url=base, headers={"Authorization": f"Bearer {key}"}, timeout=180)
        for k, meta in documents.items():
            p = resolve_file(meta["file"]) if v2 else ROOT / meta["file"]
            for _attempt in range(
                8
            ):  # the upload route is rate limited per user (30/min): wait as told, then retry
                r = await http.post("/v1/documents", files={"file": (p.name, p.read_bytes())})
                if r.status_code != 429:
                    break
                await asyncio.sleep(min(float(r.headers.get("retry-after", "5")) + 1, 65))
            r.raise_for_status()
            doc_ids[k] = r.json()["document_id"]
        deadline = (
            time.monotonic() + 2400
        )  # ~75 documents incl. 10-Ks through the ingest workers (4 at a time)
        for k, did in doc_ids.items():
            while True:
                st = (await http.get(f"/v1/documents/{did}")).json()["status"]
                if st in READY:
                    break
                if st in {"failed", "unsupported", "needs_ocr"} or time.monotonic() > deadline:
                    raise RuntimeError(f"document {k} ended in {st}")
                await asyncio.sleep(2)
        if reuse_path and v2:
            _save_reuse(reuse_path, key, uid, doc_ids)
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
    for c in chunks_from_rows([dict(r) for r in rows if str(r["document_id"]) in by_id], by_id):
        chunks[c.doc_id].append(c)

    seen_runs: set[uuid.UUID] = set()  # rag_runs of this process's turns: the server's record of the config

    async def new_session(q: Query, ctx: EvalContext) -> uuid.UUID:
        body = {"document_ids": [ctx.doc_ids[d] for d in q.scope_docs]} if q.scope_docs else {}
        r = await http.post("/v1/chat/sessions", json=body)
        r.raise_for_status()
        return uuid.UUID(r.json()["id"])

    async def pipeline(q: Query, ctx: EvalContext) -> ItemOutput:
        sid = ctx.session_id or await new_session(q, ctx)
        t0 = time.monotonic()
        body = {
            "content": q.question,
            "client_message_id": f"{q.item_id}:{q.turn_index}:{uuid.uuid4().hex[:8]}",
        }
        ttft: float | None = None
        if stream:
            async with http.stream(
                "POST",
                f"/v1/chat/sessions/{sid}/messages",
                json=body,
                headers={"Accept": "text/event-stream"},
            ) as r:
                r.raise_for_status()
                res = await read_turn_stream(r.aiter_lines(), t0)
            if res.error or res.message_id is None:
                raise RuntimeError(f"stream ended without done: {res.error or res.events}")
            ms, ttft, msg_id = res.latency_ms or 0.0, res.ttft_ms, res.message_id
        else:
            for _attempt in range(6):  # the chat route is rate limited per user (20/min): wait as told, retry
                t0 = time.monotonic()
                r = await http.post(f"/v1/chat/sessions/{sid}/messages", json=body)
                if r.status_code != 429:
                    break
                await asyncio.sleep(min(float(r.headers.get("retry-after", "5")) + 1, 65))
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
            seen_runs.add(run[0])
            call_ids = (
                (
                    await s.execute(
                        text("SELECT id FROM llm_calls WHERE run_id = :r"),
                        {"r": run[0]},
                    )
                )
                .scalars()
                .all()
            )
            cand_rows = (
                await s.execute(
                    text(
                        "SELECT chunk_id, attempt, selected, rerank_score, vector_distance, id_match "
                        "FROM retrieval_candidates "
                        "WHERE run_id = :r ORDER BY "
                        "rerank_score DESC NULLS LAST, rrf_score DESC NULLS LAST"
                    ),
                    {"r": run[0]},
                )
            ).all()
            abstain_run = (
                await s.execute(
                    text("SELECT analysis -> 'abstain' FROM rag_runs WHERE id = :r AND owner_id = :o"),
                    {"r": run[0], "o": uid},
                )
            ).scalar_one_or_none()
        cands = [r[0] for r in cand_rows]
        abstained = msg["response_type"] in {"abstain", "clarify"}
        kw: dict[str, Any] = {
            "retrieved": [str(c) for c in cands],
            "segments": [] if abstained and not segs else segs,
            "abstained": abstained,
            "latency_ms": ms,
            "ttft_ms": ttft,
            "cost_usd": float(run[1] or 0),
            "call_ids": tuple(str(c) for c in call_ids),
            "verdicts": tuple(
                seg["verification"] for seg in msg["segments"] if seg.get("verification") in _VERDICTS
            ),
        }
        if not v2:
            return ItemOutput(**kw)
        from eval.run_v2 import ItemOutputV2

        last = max((r[1] for r in cand_rows), default=0)  # the final attempt's selected chunks + cited ones
        picked = {str(r[0]) for r in cand_rows if r[2] and r[1] == last}
        picked |= {c.chunk_id for sg in segs for c in sg.citations}
        inputs = {  # the final attempt's scores and the features the turn recorded
            "candidates": [
                {"rerank_score": r[3], "vector_distance": r[4], "id_match": bool(r[5])}
                for r in cand_rows
                if r[1] == last
            ],
            "run": abstain_run,
        }
        return ItemOutputV2(**kw, evidence=sorted(picked), abstain_inputs=inputs)

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

    async def answer_config() -> dict[str, Any] | None:
        """What the server recorded for this run's turns (`rag_runs.config`) and for the ingested documents
        (`document_versions.chunker_version`, contextual prefixes): not local settings."""
        if not seen_runs:
            return None
        async with sm() as s:
            cfgs = (
                (
                    await s.execute(
                        text("SELECT config FROM rag_runs WHERE owner_id = :o AND id = ANY(:ids)"),
                        {"o": uid, "ids": list(seen_runs)},
                    )
                )
                .scalars()
                .all()
            )
            chunkers = (
                (
                    await s.execute(
                        text(
                            "SELECT DISTINCT coalesce(chunker_version, 'unknown') FROM document_versions "
                            "WHERE owner_id = :o AND document_id = ANY(:docs)"
                        ),
                        {"o": uid, "docs": [uuid.UUID(d) for d in doc_ids.values()]},
                    )
                )
                .scalars()
                .all()
            )
            ctx_prefix = (
                await s.execute(
                    text("SELECT bool_or(contextual_prefix IS NOT NULL) FROM chunks WHERE owner_id = :o"),
                    {"o": uid},
                )
            ).scalar_one()
        index = {
            "chunker": "|".join(sorted(chunkers)),
            "contextual_prefix": bool(ctx_prefix),
        }
        return merge_answer_config([c for c in cfgs if c], index)

    judges = _JudgeScorer() if with_judges else None
    if v2:
        units_cache: dict[str, list[str] | None] = {}

        def gold_units(doc: str) -> list[str] | None:
            if doc not in units_cache:
                try:
                    units_cache[doc] = document_units(doc, v2_docs[doc]) if doc in v2_docs else None
                except Exception as e:  # unknown gold is reported as not recorded, never as a failure
                    print(f"gold sentences unavailable for {doc}: {type(e).__name__}: {e}")
                    units_cache[doc] = None
            return units_cache[doc]

        return Harness(
            pipeline,
            chunks,
            EvalContext(owner_id=uid, doc_ids=doc_ids),
            new_session,
            spent=spent,
            close=http.aclose,
            judge_gw=judges.gw if judges else None,
            judge_guard=judges.guard if judges else None,
            gold_units=gold_units,
            answer_config=answer_config,
        )
    return Harness(
        pipeline,
        chunks,
        EvalContext(owner_id=uid, doc_ids=doc_ids),
        new_session,
        extra_scorer=judges.score if judges else None,
        spent=spent,
        close=http.aclose,
        answer_config=answer_config,
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
