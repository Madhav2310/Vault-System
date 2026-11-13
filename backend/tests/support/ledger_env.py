"""Seed a finished-turn world for ledger/history tests: owner, scope document + chunk, a started turn (via the
real `start_turn`), an llm call, and a `TurnRecord`. `ledger_owners` also removes the ledger rows, which the
shared teardown does not know, before the owners are deleted."""

import datetime as dt
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.chat import sessions as svc
from app.chat.citations import EvidenceReference, Resolution, ResolvedSegment
from app.chat.ledger import CandidateRecord, StepRecord, TurnRecord
from app.chat.nodes.generate import GenerateResult
from app.llm.types import Usage
from tests.support.docs import Seeded, seed_chunk, seed_document

CONFIG: dict[str, Any] = {
    "prompts": ["answer_rag@1"],
    "models": {"balanced": "gpt-6.1-terra"},
    "retrieval": "retrieval@1",
}


@pytest.fixture
async def ledger_owners(
    owners: Callable[[], Any], sm: async_sessionmaker[AsyncSession]
) -> AsyncIterator[Callable[[], Any]]:
    made: list[uuid.UUID] = []

    async def mk() -> Any:
        o = await owners()
        made.append(o.id)
        return o

    yield mk
    async with sm() as s:
        p = {"ids": made}
        runs = "(SELECT id FROM rag_runs WHERE owner_id = ANY(:ids))"
        for stmt in (
            f"DELETE FROM segment_evidence WHERE segment_id IN "
            f"(SELECT id FROM answer_segments WHERE run_id IN {runs})",
            f"DELETE FROM answer_segments WHERE run_id IN {runs}",
            f"DELETE FROM retrieval_candidates WHERE run_id IN {runs}",
            f"DELETE FROM run_steps WHERE run_id IN {runs}",
            f"DELETE FROM run_versions WHERE run_id IN {runs}",
            "DELETE FROM llm_calls WHERE owner_id = ANY(:ids)",
        ):
            await s.execute(text(stmt), p)
        await s.commit()


@dataclass(frozen=True)
class World:
    owner: Any
    session_id: uuid.UUID
    run_id: uuid.UUID
    user_message_id: uuid.UUID
    doc: Seeded
    chunk_id: uuid.UUID
    other_chunk_id: uuid.UUID  # same doc, second chunk


async def make_world(
    sm: async_sessionmaker[AsyncSession], owner: Any, *, client_message_id: str = "m1", content: str = "Q?"
) -> World:
    doc = await seed_document(sm, owner.id, title="Master Agreement")
    c1 = await seed_chunk(sm, owner.id, doc, ordinal=0, section_path=["1", "Scope"], page_start=2, page_end=3)
    c2 = await seed_chunk(sm, owner.id, doc, ordinal=1)
    async with sm() as s:
        row = (
            await s.execute(
                text("INSERT INTO chat_sessions (id, owner_id) VALUES (gen_random_uuid(), :o) RETURNING id"),
                {"o": owner.id},
            )
        ).scalar_one()
        await s.commit()
    async with sm() as s:
        t = await svc.start_turn(s, owner.id, row, content, client_message_id)
    return World(owner, row, t.run_id, t.user_message_id, doc, c1, c2)


async def add_call(
    sm: async_sessionmaker[AsyncSession],
    owner_id: uuid.UUID,
    *,
    inp: int = 100,
    out: int = 20,
    cache: int = 5,
    cost: str = "0.0123",
    run_id: uuid.UUID | None = None,
) -> uuid.UUID:
    cid = uuid.uuid4()
    async with sm() as s:
        await s.execute(
            text(
                "INSERT INTO llm_calls (id, owner_id, run_id, purpose, lane, provider, model, attempt, "
                "input_tokens, output_tokens, cache_read_tokens, cost_usd, status) VALUES "
                "(:id, :o, :r, 'generate', 'chat', 'openai', 'gpt-6.1-terra', 1, :i, :ou, :c, :cost, 'ok')"
            ),
            {"id": cid, "o": owner_id, "r": run_id, "i": inp, "ou": out, "c": cache, "cost": Decimal(cost)},
        )
        await s.commit()
    return cid


def generation(w: World, call_ids: tuple[uuid.UUID, ...] = ()) -> GenerateResult:
    ref = EvidenceReference(w.chunk_id, w.doc.version_id, 0, 1, 0, 11, "Alpha beta.")
    res = Resolution(
        (
            ResolvedSegment(0, "Alpha is beta.", "cited", (ref,)),
            ResolvedSegment(1, "Nothing else is known.", "none", ()),
        )
    )
    return GenerateResult("answer", res, (), Usage(), Decimal(0), call_ids)


def record(w: World, call_ids: tuple[uuid.UUID, ...] = (), **kw: Any) -> TurnRecord:
    base: dict[str, Any] = {
        "run_id": w.run_id,
        "route": "lookup",
        "mode": "auto",
        "status": "completed",
        "scope_version_ids": (w.doc.version_id,),
        "generation": generation(w, call_ids),
        "config": CONFIG,
        "candidates": (
            CandidateRecord(
                w.chunk_id,
                vector_rank=1,
                vector_distance=0.12,
                lexical_rank=2,
                lexical_score=3.5,
                id_match=True,
                rrf_score=0.03,
                rerank_score=0.9,
                selected=True,
                result_index=0,
            ),
            CandidateRecord(w.other_chunk_id, vector_rank=2, vector_distance=0.3, expanded_from=w.chunk_id),
        ),
        "steps": (
            StepRecord(0, "analyze", "ok", dt.datetime.now(dt.UTC), 5, {"route": "lookup"}),
            StepRecord(1, "generate", "ok", dt.datetime.now(dt.UTC), 900, None, "ANSWERED"),
        ),
        "call_ids": call_ids,
        "coverage_state": "covered",
        "latency_ms": 1000,
    }
    base.update(kw)
    return TurnRecord(**base)
