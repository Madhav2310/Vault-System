"""Evidence ledger persistence (design 8.3, 11): one transaction per finished turn.

`persist_turn` completes the `running` rag_run that `sessions.start_turn` created and writes everything the
turn produced (assistant message, segments, evidence, run versions, steps, candidates, llm call links), then
releases the session lease, all in ONE transaction: a failure leaves no partial rows and the lease untouched.
It is idempotent: a run that already has an assistant message returns that message and writes nothing.

Invariants enforced here (not just tested): every cited and every candidate chunk is the owner's and belongs
to a version in the run's scope snapshot; run totals are summed from the linked `llm_calls` rows. No document
text is logged; `cited_text` and segment text are stored, never emitted.
"""

import datetime as dt
import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from opentelemetry import trace
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.nodes.generate import GenerateResult
from app.core.ids import uuid7

CONFIG_KEYS = ("prompts", "models", "retrieval")


class LedgerError(Exception):
    """An invariant of the evidence ledger does not hold; nothing was written."""


class RunNotFound(LedgerError):
    pass


@dataclass(frozen=True)
class CandidateRecord:
    """One retrieval candidate with all provenance. `vector_distance` is the raw distance."""

    chunk_id: uuid.UUID
    attempt: int = 1
    vector_rank: int | None = None
    vector_distance: float | None = None
    lexical_rank: int | None = None
    lexical_score: float | None = None
    id_match: bool = False
    rrf_score: float | None = None
    rerank_score: float | None = None
    selected: bool = False
    expanded_from: uuid.UUID | None = None
    result_index: int | None = None


@dataclass(frozen=True)
class StepRecord:
    seq: int
    node: str
    status: str
    started_at: dt.datetime
    duration_ms: int | None = None
    decision: dict[str, Any] | None = None
    reason_code: str | None = None
    error_code: str | None = None


@dataclass(frozen=True)
class TurnRecord:
    """`config` must hold `prompts` (list of "id@version"), `models` (role -> model) and `retrieval`
    ("retrieval@v"). `trace_id` defaults to the current OTel span's trace id (32 hex chars)."""

    run_id: uuid.UUID
    route: str
    mode: str
    status: str  # anything but 'running'
    scope_version_ids: tuple[uuid.UUID, ...]
    generation: GenerateResult
    config: dict[str, Any]
    candidates: tuple[CandidateRecord, ...] = ()
    steps: tuple[StepRecord, ...] = ()
    call_ids: tuple[uuid.UUID, ...] = ()
    trace_id: str | None = None
    coverage_state: str | None = None
    standalone_query: str | None = None
    analysis: dict[str, Any] | None = None
    coverage: dict[str, Any] | None = None
    conflicts: dict[str, Any] | None = None
    reason_codes: tuple[str, ...] | None = None  # default: generation.reasons
    degraded: tuple[str, ...] = field(default=())
    limits: dict[str, Any] | None = None
    latency_ms: int | None = None
    ttft_ms: int | None = None


@dataclass(frozen=True)
class PersistedTurn:
    run_id: uuid.UUID
    assistant_message_id: uuid.UUID
    created: bool  # False when the run was already persisted (no-op)


def current_trace_id() -> str | None:
    ctx = trace.get_current_span().get_span_context()
    return format(ctx.trace_id, "032x") if ctx.is_valid else None


def _j(v: Any) -> str | None:
    return None if v is None else json.dumps(v)


def _validate(rec: TurnRecord) -> None:
    if rec.status == "running":
        raise LedgerError("a persisted run cannot be 'running'")
    missing = [k for k in CONFIG_KEYS if k not in rec.config]
    if missing:
        raise LedgerError(f"config is missing {missing}")


async def _lock_run(db: AsyncSession, owner_id: uuid.UUID, run_id: uuid.UUID) -> Any:
    r = (
        (
            await db.execute(
                text(
                    "SELECT id, session_id, assistant_message_id FROM rag_runs "
                    "WHERE id = :r AND owner_id = :o FOR UPDATE"
                ),
                {"r": run_id, "o": owner_id},
            )
        )
        .mappings()
        .first()
    )
    if r is None:
        raise RunNotFound(str(run_id))
    return r


async def _insert_versions(db: AsyncSession, owner_id: uuid.UUID, rec: TurnRecord) -> None:
    ids = list(dict.fromkeys(rec.scope_version_ids))
    res = await db.execute(
        text(
            "INSERT INTO run_versions (run_id, version_id) "
            "SELECT :r, id FROM document_versions WHERE id = ANY(:v) AND owner_id = :o"
        ),
        {"r": rec.run_id, "v": ids, "o": owner_id},
    )
    if res.rowcount != len(ids):  # type: ignore[attr-defined]
        raise LedgerError("a scope version does not exist or is not the owner's")


async def _check_chunks(db: AsyncSession, owner_id: uuid.UUID, rec: TurnRecord) -> None:
    """Every cited/candidate chunk is the owner's and sits in a version of the scope snapshot."""
    scope = set(rec.scope_version_ids)
    cited = {
        ref.chunk_id: ref.version_id for seg in rec.generation.resolution.segments for ref in seg.references
    }
    wanted = set(cited) | {c.chunk_id for c in rec.candidates}
    wanted |= {c.expanded_from for c in rec.candidates if c.expanded_from is not None}
    if not wanted:
        return
    rows = (
        await db.execute(
            text("SELECT id, version_id FROM chunks WHERE owner_id = :o AND id = ANY(:ids)"),
            {"o": owner_id, "ids": list(wanted)},
        )
    ).all()
    actual = {r[0]: r[1] for r in rows}
    for cid in wanted:
        if cid not in actual:
            raise LedgerError("a chunk does not exist or is not the owner's")
        if actual[cid] not in scope:
            raise LedgerError("a chunk is outside the run's version snapshot")
    for cid, vid in cited.items():
        if actual[cid] != vid:
            raise LedgerError("a citation names the wrong version for its chunk")


async def _insert_segments(
    db: AsyncSession, rec: TurnRecord, message_id: uuid.UUID
) -> list[tuple[uuid.UUID, Any]]:
    out: list[tuple[uuid.UUID, Any]] = []
    for seg in rec.generation.resolution.segments:
        sid = uuid7()
        await db.execute(
            text(
                "INSERT INTO answer_segments (id, run_id, message_id, ordinal, text, support, "
                "verification, verification_reason) VALUES (:id, :r, :m, :ord, :t, :s, :v, :vr)"
            ),
            {
                "id": sid,
                "r": rec.run_id,
                "m": message_id,
                "ord": seg.ordinal,
                "t": seg.text,
                "s": seg.support,
                "v": seg.verification,
                "vr": seg.verification_reason,
            },
        )
        out.append((sid, seg))
    return out


async def _insert_evidence(db: AsyncSession, segments: list[tuple[uuid.UUID, Any]]) -> None:
    rows = [
        {"s": sid, "c": ref.chunk_id, "a": ref.sentence_start, "b": ref.sentence_end, "t": ref.cited_text}
        for sid, seg in segments
        for ref in seg.references
    ]
    if rows:
        await db.execute(
            text(
                "INSERT INTO segment_evidence "
                "(segment_id, chunk_id, sentence_start, sentence_end, cited_text) "
                "VALUES (:s, :c, :a, :b, :t)"
            ),
            rows,
        )


async def _insert_steps(db: AsyncSession, rec: TurnRecord) -> None:
    if rec.steps:
        await db.execute(
            text(
                "INSERT INTO run_steps (id, run_id, seq, node, status, started_at, duration_ms, decision, "
                "reason_code, error_code) VALUES (:id, :r, :seq, :n, :st, :at, :d, CAST(:dec AS jsonb), "
                ":rc, :ec)"
            ),
            [
                {
                    "id": uuid7(),
                    "r": rec.run_id,
                    "seq": s.seq,
                    "n": s.node,
                    "st": s.status,
                    "at": s.started_at,
                    "d": s.duration_ms,
                    "dec": _j(s.decision),
                    "rc": s.reason_code,
                    "ec": s.error_code,
                }
                for s in rec.steps
            ],
        )


async def _insert_candidates(db: AsyncSession, rec: TurnRecord) -> None:
    if rec.candidates:
        await db.execute(
            text(
                "INSERT INTO retrieval_candidates (run_id, attempt, chunk_id, vector_rank, vector_distance, "
                "lexical_rank, lexical_score, id_match, rrf_score, rerank_score, selected, expanded_from, "
                "result_index) VALUES (:r, :at, :c, :vr, :vd, :lr, :ls, :im, :rrf, :rr, :sel, :ef, :ri)"
            ),
            [
                {
                    "r": rec.run_id,
                    "at": c.attempt,
                    "c": c.chunk_id,
                    "vr": c.vector_rank,
                    "vd": c.vector_distance,
                    "lr": c.lexical_rank,
                    "ls": c.lexical_score,
                    "im": c.id_match,
                    "rrf": c.rrf_score,
                    "rr": c.rerank_score,
                    "sel": c.selected,
                    "ef": c.expanded_from,
                    "ri": c.result_index,
                }
                for c in rec.candidates
            ],
        )


async def _link_calls_and_totals(db: AsyncSession, owner_id: uuid.UUID, rec: TurnRecord) -> None:
    ids = list(dict.fromkeys(rec.call_ids))
    if ids:
        res = await db.execute(
            text(
                "UPDATE llm_calls SET run_id = :r WHERE id = ANY(:ids) AND owner_id = :o "
                "AND (run_id IS NULL OR run_id = :r)"
            ),
            {"r": rec.run_id, "ids": ids, "o": owner_id},
        )
        if res.rowcount != len(ids):  # type: ignore[attr-defined]
            raise LedgerError("an llm call does not exist, is not the owner's, or belongs to another run")
    # Totals are summed from every call linked to the run (the gateway may have linked some already).
    await db.execute(
        text(
            "UPDATE rag_runs SET "
            "(llm_calls_used, input_tokens, output_tokens, cache_read_tokens, cost_usd) = "
            "(SELECT count(*), COALESCE(sum(input_tokens), 0), COALESCE(sum(output_tokens), 0), "
            "COALESCE(sum(cache_read_tokens), 0), COALESCE(sum(cost_usd), 0) "
            "FROM llm_calls WHERE run_id = :r AND owner_id = :o) WHERE id = :r AND owner_id = :o"
        ),
        {"r": rec.run_id, "o": owner_id},
    )


async def persist_turn(db: AsyncSession, owner_id: uuid.UUID, rec: TurnRecord) -> PersistedTurn:
    """Persist a finished turn atomically; idempotent per `rec.run_id`.

    Raises LedgerError / RunNotFound (nothing written, session lease untouched)."""
    _validate(rec)
    try:
        run = await _lock_run(db, owner_id, rec.run_id)
        if run["assistant_message_id"] is not None:
            await db.rollback()
            return PersistedTurn(rec.run_id, run["assistant_message_id"], False)
        session_id: uuid.UUID = run["session_id"]
        msg_id = uuid7()
        await _insert_versions(db, owner_id, rec)
        await _check_chunks(db, owner_id, rec)
        await db.execute(
            text(
                "INSERT INTO chat_messages (id, owner_id, session_id, role, content, response_type, "
                "coverage_state, run_id, followups) "
                "VALUES (:id, :o, :s, 'assistant', :c, :rt, :cs, :r, CAST(:fu AS jsonb))"
            ),
            {
                "id": msg_id,
                "o": owner_id,
                "s": session_id,
                "c": rec.generation.text,
                "rt": rec.generation.response_type,
                "cs": rec.coverage_state,
                "r": rec.run_id,
                "fu": json.dumps(list(rec.generation.followups)),
            },
        )
        segments = await _insert_segments(db, rec, msg_id)
        await _insert_evidence(db, segments)
        await _insert_steps(db, rec)
        await _insert_candidates(db, rec)
        await _link_calls_and_totals(db, owner_id, rec)
        reasons = list(rec.reason_codes if rec.reason_codes is not None else rec.generation.reasons)
        await db.execute(
            text(
                "UPDATE rag_runs SET route = :route, mode = :mode, status = :st, trace_id = :tr, "
                "standalone_query = :sq, analysis = CAST(:an AS jsonb), coverage = CAST(:cov AS jsonb), "
                "conflicts = CAST(:conf AS jsonb), reason_codes = :rc, degraded = :dg, "
                "limits = CAST(:lim AS jsonb), config = CAST(:cfg AS jsonb), latency_ms = :lat, "
                "ttft_ms = :ttft, finished_at = now(), assistant_message_id = :m "
                "WHERE id = :r AND owner_id = :o"
            ),
            {
                "route": rec.route,
                "mode": rec.mode,
                "st": rec.status,
                "tr": rec.trace_id or current_trace_id(),
                "sq": rec.standalone_query,
                "an": _j(rec.analysis),
                "cov": _j(rec.coverage),
                "conf": _j(rec.conflicts),
                "rc": reasons,
                "dg": list(rec.degraded),
                "lim": _j(rec.limits),
                "cfg": json.dumps(rec.config),
                "lat": rec.latency_ms,
                "ttft": rec.ttft_ms,
                "m": msg_id,
                "r": rec.run_id,
                "o": owner_id,
            },
        )
        # Fenced lease release, last step of the same transaction (a lost fence releases nothing).
        await db.execute(
            text(
                "UPDATE chat_sessions SET active_run_id = NULL, run_lease_expires_at = NULL, "
                "updated_at = now() WHERE id = :s AND owner_id = :o AND active_run_id = :r"
            ),
            {"s": session_id, "o": owner_id, "r": rec.run_id},
        )
        await db.commit()
    except BaseException:
        await db.rollback()
        raise
    return PersistedTurn(rec.run_id, msg_id, True)
