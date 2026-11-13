"""Turn orchestrator (design §8): analyze -> scope -> retrieve -> evidence -> generate -> resolve -> persist.

The orchestrator, not a node, picks the next node. No DB connection is held across an LLM call or a
retrieval call: each phase opens its own short session. Whatever happens, the run is finished and the session
lease released in one `persist_turn` transaction (failures become an `error` response, never a stuck run).

M0 scope: no evidence gate, no full-context route, no conflict detection (later slices); `mode=full_context`
is recorded but answered through RAG with reason code `FULL_CONTEXT_UNAVAILABLE`.
"""

import asyncio
import dataclasses
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.chat import sessions as svc
from app.chat.citations import Resolution, ResolvedSegment
from app.chat.evidence import build_evidence
from app.chat.harness import DEFAULT_LIMITS, ROUTE_LIMITS, TurnBudget
from app.chat.ledger import CandidateRecord, PersistedTurn, StepRecord, TurnRecord, persist_turn
from app.chat.memory import load_memory
from app.chat.nodes.analyze import AnalyzeResult, ScopeDoc, analyze_query
from app.chat.nodes.generate import GenerateResult, generate
from app.core.logging import get_logger
from app.llm.errors import LlmError
from app.llm.gateway import LlmGateway
from app.llm.types import Usage
from app.retrieval.load import load_evidence_chunks
from app.retrieval.retrieve import RetrievalResult, Retriever
from app.retrieval.scope import Scope, ScopeWideningError, compute_scope, narrow

_log = get_logger("vault.chat.turn")
SessionMaker = async_sessionmaker[AsyncSession]

NO_DOCUMENTS = "SCOPE_EMPTY"
NO_EVIDENCE = "NO_EVIDENCE"
TURN_ERROR = "TURN_ERROR"
FULL_CONTEXT_UNAVAILABLE = "FULL_CONTEXT_UNAVAILABLE"
TARGET_IGNORED = "TARGET_IGNORED"
HEARTBEAT_S = 30.0
NO_DOCUMENTS_TEXT = "No ready documents are in scope. Upload a document or wait for processing to finish."
NO_EVIDENCE_TEXT = "I could not find anything in your documents that answers this."
ERROR_TEXT = "Something went wrong while answering. Please try again."


@dataclass(frozen=True)
class TurnDeps:
    sm: SessionMaker
    gateway: LlmGateway
    retriever: Retriever


def _plain(response_type: str, text_: str, reasons: tuple[str, ...], res: Any = None) -> GenerateResult:
    """A response written by code (no model call, no citations)."""
    seg = ResolvedSegment(0, text_, "none", ())
    return GenerateResult(
        response_type, Resolution((seg,)), reasons, Usage(), Decimal(0), tuple(getattr(res, "call_ids", ()))
    )


class _Steps:
    def __init__(self) -> None:
        self.items: list[StepRecord] = []

    def add(self, node: str, status: str, started: datetime, t0: float, **kw: Any) -> None:
        self.items.append(
            StepRecord(len(self.items) + 1, node, status, started, int((time.monotonic() - t0) * 1000), **kw)
        )


def _candidates(r: RetrievalResult) -> tuple[CandidateRecord, ...]:
    return tuple(
        CandidateRecord(
            chunk_id=c.chunk_id,
            vector_rank=c.vector_rank,
            vector_distance=c.vector_distance,
            lexical_rank=c.lexical_rank,
            lexical_score=c.lexical_score,
            id_match=c.id_match,
            rrf_score=c.rrf_score,
            rerank_score=c.rerank_score,
            selected=c.selected,
            expanded_from=c.expanded_from,
            result_index=c.result_index,
        )
        for c in r.candidates
    )


async def _scope_docs(s: AsyncSession, owner_id: uuid.UUID, scope: Scope) -> list[ScopeDoc]:
    rows = await s.execute(
        text("SELECT id, title FROM documents WHERE owner_id = :o AND id = ANY(:ids) AND deleted_at IS NULL"),
        {"o": owner_id, "ids": list(scope.document_ids)},
    )
    titles = {r[0]: r[1] for r in rows}
    return [ScopeDoc(d, titles[d]) for d in scope.document_ids if d in titles]


async def _run_turn(
    deps: TurnDeps,
    owner_id: uuid.UUID,
    session_id: uuid.UUID,
    run_id: uuid.UUID,
    user_message_id: uuid.UUID,
    question: str,
    mode: str = "auto",
) -> PersistedTurn:
    """Run one turn to completion and persist it. Idempotent per run (a finished run is returned as is)."""
    t_start = time.monotonic()
    steps = _Steps()
    budget = TurnBudget(DEFAULT_LIMITS)
    route, status = "qa", "completed"
    scope = Scope.empty
    analysis: AnalyzeResult | None = None
    retrieval: RetrievalResult | None = None
    gen: GenerateResult
    degraded: list[str] = []
    extra_reasons: list[str] = []
    call_ids: list[uuid.UUID] = []
    try:
        async with deps.sm() as s:
            sess = await svc.get_session(s, owner_id, session_id)
            scope = await compute_scope(s, owner_id, sess.document_ids)
            memory = await load_memory(s, owner_id, session_id, exclude_message_id=user_message_id)
            docs = await _scope_docs(s, owner_id, scope)
        if mode == "full_context":
            extra_reasons.append(FULL_CONTEXT_UNAVAILABLE)
        if not scope.entries:
            route = "meta"
            gen = _plain("abstain", NO_DOCUMENTS_TEXT, (NO_DOCUMENTS,))
        else:
            t0, at = time.monotonic(), datetime.now(UTC)
            analysis = await analyze_query(
                deps.gateway, question=question, memory=memory, documents=docs, owner_id=owner_id,
                run_id=run_id, budget=budget,
            )  # fmt: skip
            steps.add("analyze", "ok", at, t0, decision={"intent": analysis.intent})
            degraded += analysis.degraded
            call_ids += analysis.call_ids
            route = analysis.intent if analysis.intent in ROUTE_LIMITS else "qa"
            budget = TurnBudget(ROUTE_LIMITS[route], budget.deadline_at, budget.calls_used)
            if analysis.clarification is not None:
                c = analysis.clarification
                body = c.question + (" " + " / ".join(c.options) if c.options else "")
                gen = _plain("clarify", body, analysis.reason_codes)
            else:
                try:
                    scope = narrow(scope, analysis.target_document_ids)
                except ScopeWideningError:  # never widen; keep the wider scope but leave a trace
                    extra_reasons.append(TARGET_IGNORED)
                gen, retrieval = await _answer(deps, owner_id, run_id, analysis, scope, memory, budget, steps)
                degraded += retrieval.degraded if retrieval else []
    except Exception as e:  # the turn must always finish; log type only
        _log.error("turn_failed", error_type=type(e).__name__, code=getattr(e, "error_type", None))
        status = "failed"
        code = getattr(e, "error_type", None)
        gen = _plain("error", ERROR_TEXT, (TURN_ERROR, code) if isinstance(code, str) else (TURN_ERROR,))
    rec = TurnRecord(
        run_id=run_id,
        route=route,
        mode="full_context" if mode == "full_context" else "rag",
        status=status,
        scope_version_ids=scope.version_ids,
        generation=gen,
        config={
            "prompts": ["analyze_query@1", "answer_rag@1"],
            "models": _models(deps.gateway),
            "retrieval": deps.retriever.config.version,
        },
        candidates=_candidates(retrieval) if retrieval else (),
        steps=tuple(steps.items),
        call_ids=tuple(dict.fromkeys([*call_ids, *gen.call_ids])),
        standalone_query=analysis.standalone_query if analysis else None,
        analysis={"intent": analysis.intent, "dimensions": list(analysis.dimensions)} if analysis else None,
        reason_codes=(*gen.reasons, *extra_reasons),
        degraded=tuple(dict.fromkeys(degraded)),
        limits={"max_llm_calls": budget.limits.max_llm_calls, "deadline_s": budget.limits.deadline_s},
        latency_ms=int((time.monotonic() - t_start) * 1000),
    )
    try:
        async with deps.sm() as s:
            return await persist_turn(s, owner_id, rec)
    except Exception as e:
        # A ledger/DB failure must not leave the run `running` and the session leased: retry minimally.
        _log.error("persist_failed", error_type=type(e).__name__)
        minimal = dataclasses.replace(
            rec,
            status="failed",
            generation=_plain("error", ERROR_TEXT, (TURN_ERROR, "PERSIST_FAILED")),
            candidates=(),
            steps=(),
            call_ids=(),
            reason_codes=(TURN_ERROR, "PERSIST_FAILED"),
        )
        try:
            async with deps.sm() as s:
                return await persist_turn(s, owner_id, minimal)
        except Exception as e2:
            _log.error("persist_fallback_failed", error_type=type(e2).__name__)
            async with deps.sm() as s:
                await svc.release_turn(s, session_id, owner_id, run_id)
            raise


async def run_turn(
    deps: TurnDeps,
    owner_id: uuid.UUID,
    session_id: uuid.UUID,
    run_id: uuid.UUID,
    user_message_id: uuid.UUID,
    question: str,
    mode: str = "auto",
) -> PersistedTurn:
    """`_run_turn` with a lease heartbeat, so a slow turn keeps its session while it runs."""

    async def beat() -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_S)
            async with deps.sm() as s:
                if not await svc.extend_lease(s, session_id, owner_id, run_id):
                    return

    hb = asyncio.ensure_future(beat())
    try:
        return await _run_turn(deps, owner_id, session_id, run_id, user_message_id, question, mode)
    finally:
        hb.cancel()


async def _answer(
    deps: TurnDeps,
    owner_id: uuid.UUID,
    run_id: uuid.UUID,
    analysis: AnalyzeResult,
    scope: Scope,
    memory: Any,
    budget: TurnBudget,
    steps: _Steps,
) -> tuple[GenerateResult, RetrievalResult | None]:
    t0, at = time.monotonic(), datetime.now(UTC)
    async with deps.sm() as s:
        retrieval = await deps.retriever.retrieve(s, owner_id, scope, analysis.standalone_query)
        chunks = await load_evidence_chunks(s, owner_id, list(retrieval.selected))
    steps.add(
        "retrieve", "ok", at, t0, decision={"selected": len(chunks), "degraded": list(retrieval.degraded)}
    )
    if not chunks:
        return _plain("abstain", NO_EVIDENCE_TEXT, (NO_EVIDENCE,)), retrieval
    evidence = build_evidence(chunks)
    t0, at = time.monotonic(), datetime.now(UTC)
    try:
        gen = await generate(
            deps.gateway, owner_id=owner_id, evidence=evidence, question=analysis.standalone_query,
            route="qa", memory=memory, run_id=run_id, budget=budget,
        )  # fmt: skip
    except LlmError as e:
        steps.add("generate", "failed", at, t0, error_code=e.error_type)
        return _plain("error", ERROR_TEXT, (TURN_ERROR, e.error_type)), retrieval
    steps.add("generate", "ok", at, t0, decision={"response_type": gen.response_type})
    return gen, retrieval


def _models(gw: LlmGateway) -> dict[str, str]:
    s = gw.settings
    return {
        "fast": s.model_fast,
        "balanced": s.model_balanced,
        "deep": s.model_deep,
        "flagship": s.model_flagship,
    }
