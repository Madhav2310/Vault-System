"""S3.10 evidence ledger persistence."""

import uuid
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest
from opentelemetry.sdk.trace import TracerProvider
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.chat import ledger
from app.chat.ledger import LedgerError, persist_turn
from tests.support.ledger_env import CONFIG, World, add_call, ledger_owners, make_world, record

__all__ = ["ledger_owners"]  # fixture import


async def _scalar(sm: async_sessionmaker[AsyncSession], sql: str, **p: Any) -> Any:
    async with sm() as s:
        return (await s.execute(text(sql), p)).scalar_one()


async def _counts(sm: async_sessionmaker[AsyncSession], run_id: uuid.UUID) -> dict[str, int]:
    out = {}
    async with sm() as s:
        for name, sql in {
            "messages": "SELECT count(*) FROM chat_messages WHERE run_id = :r",
            "segments": "SELECT count(*) FROM answer_segments WHERE run_id = :r",
            "evidence": "SELECT count(*) FROM segment_evidence WHERE segment_id IN "
            "(SELECT id FROM answer_segments WHERE run_id = :r)",
            "versions": "SELECT count(*) FROM run_versions WHERE run_id = :r",
            "steps": "SELECT count(*) FROM run_steps WHERE run_id = :r",
            "candidates": "SELECT count(*) FROM retrieval_candidates WHERE run_id = :r",
        }.items():
            out[name] = (await s.execute(text(sql), {"r": run_id})).scalar_one()
    return out


async def _world(sm: async_sessionmaker[AsyncSession], owners: Callable[[], Any]) -> World:
    return await make_world(sm, await owners())


async def test_row_counts(sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any]) -> None:
    w = await _world(sm, ledger_owners)
    c = await add_call(sm, w.owner.id)
    async with sm() as s:
        first = await persist_turn(s, w.owner.id, record(w, (c,)))
    assert first.created
    n = await _counts(sm, w.run_id)
    # run (1) + user message (from start_turn) + assistant message; 2 segments, 1 evidence, 1 version
    assert await _scalar(sm, "SELECT count(*) FROM rag_runs WHERE session_id = :s", s=w.session_id) == 1
    assert await _scalar(sm, "SELECT count(*) FROM chat_messages WHERE session_id = :s", s=w.session_id) == 2
    assert n == {"messages": 2, "segments": 2, "evidence": 1, "versions": 1, "steps": 2, "candidates": 2}
    async with sm() as s:
        r = (
            (
                await s.execute(
                    text(
                        "SELECT status, route, assistant_message_id, finished_at FROM rag_runs WHERE id = :r"
                    ),
                    {"r": w.run_id},
                )
            )
            .mappings()
            .one()
        )
        lease = (
            await s.execute(
                text("SELECT active_run_id FROM chat_sessions WHERE id = :s"), {"s": w.session_id}
            )
        ).scalar_one()
        cand = (
            (
                await s.execute(
                    text("SELECT * FROM retrieval_candidates WHERE run_id = :r AND selected AND id_match"),
                    {"r": w.run_id},
                )
            )
            .mappings()
            .one()
        )
    assert r["status"] == "completed" and r["route"] == "lookup" and r["finished_at"] is not None
    assert r["assistant_message_id"] == first.assistant_message_id
    assert lease is None  # released as part of the same transaction
    assert (cand["vector_rank"], cand["lexical_rank"]) == (1, 2)
    assert cand["rerank_score"] == pytest.approx(0.9)
    assert (
        await _scalar(
            sm,
            "SELECT expanded_from FROM retrieval_candidates WHERE run_id = :r AND NOT selected",
            r=w.run_id,
        )
        == w.chunk_id
    )
    # idempotent: a second persist is a no-op returning the same message
    async with sm() as s:
        again = await persist_turn(s, w.owner.id, record(w, (c,)))
    assert not again.created and again.assistant_message_id == first.assistant_message_id
    assert await _counts(sm, w.run_id) == n


async def test_evidence_in_snapshot(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any]
) -> None:
    w = await _world(sm, ledger_owners)
    async with sm() as s:
        await persist_turn(s, w.owner.id, record(w))
    bad = await _scalar(
        sm,
        "SELECT count(*) FROM segment_evidence e JOIN answer_segments a ON a.id = e.segment_id "
        "JOIN chunks c ON c.id = e.chunk_id AND c.owner_id = :o "
        "WHERE a.run_id = :r AND c.version_id NOT IN (SELECT version_id FROM run_versions WHERE run_id = :r)",
        r=w.run_id,
        o=w.owner.id,
    )
    assert bad == 0
    # enforced in code: a chunk of a version outside the snapshot is refused and nothing is written
    w2 = await make_world(sm, w.owner, client_message_id="m2")
    other = await make_world(sm, w.owner, client_message_id="m3")
    rec = record(w2, scope_version_ids=(other.doc.version_id,))
    async with sm() as s:
        with pytest.raises(LedgerError, match="outside the run's version snapshot"):
            await persist_turn(s, w.owner.id, rec)
    assert (await _counts(sm, w2.run_id))["messages"] == 1  # only the user message


async def test_totals_match_calls(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any]
) -> None:
    w = await _world(sm, ledger_owners)
    pre = await add_call(
        sm, w.owner.id, inp=7, out=3, cache=1, cost="0.001", run_id=w.run_id
    )  # gateway-linked
    c1 = await add_call(sm, w.owner.id, inp=100, out=20, cache=5, cost="0.0123")
    c2 = await add_call(sm, w.owner.id, inp=50, out=10, cache=0, cost="0.0050")
    async with sm() as s:
        await persist_turn(s, w.owner.id, record(w, (c1, c2)))
    async with sm() as s:
        run = (
            (await s.execute(text("SELECT * FROM rag_runs WHERE id = :r"), {"r": w.run_id})).mappings().one()
        )
        calls = (
            await s.execute(
                text(
                    "SELECT count(*), sum(input_tokens), sum(output_tokens), sum(cache_read_tokens), "
                    "sum(cost_usd) FROM llm_calls WHERE run_id = :r"
                ),
                {"r": w.run_id},
            )
        ).one()
    assert pre and calls[0] == 3
    assert (
        (
            run["llm_calls_used"],
            run["input_tokens"],
            run["output_tokens"],
            run["cache_read_tokens"],
            run["cost_usd"],
        )
        == (3, 157, 33, 6, Decimal("0.0183"))
        == tuple(calls)
    )
    # a call of another owner cannot be linked
    w2 = await make_world(sm, await ledger_owners(), client_message_id="x")
    foreign = await add_call(sm, w2.owner.id)
    async with sm() as s:
        with pytest.raises(LedgerError):
            await persist_turn(
                s, w.owner.id, record(await make_world(sm, w.owner, client_message_id="y"), (foreign,))
            )


async def test_trace_id_linked(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any]
) -> None:
    w = await _world(sm, ledger_owners)
    tracer = TracerProvider().get_tracer("test")
    with tracer.start_as_current_span("vault.turn") as span:
        expected = format(span.get_span_context().trace_id, "032x")
        async with sm() as s:
            await persist_turn(s, w.owner.id, record(w))
    assert await _scalar(sm, "SELECT trace_id FROM rag_runs WHERE id = :r", r=w.run_id) == expected
    # an explicit trace id wins
    w2 = await make_world(sm, w.owner, client_message_id="m2")
    async with sm() as s:
        await persist_turn(s, w.owner.id, record(w2, trace_id="ab" * 16))
    assert await _scalar(sm, "SELECT trace_id FROM rag_runs WHERE id = :r", r=w2.run_id) == "ab" * 16


async def test_config_recorded(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any]
) -> None:
    w = await _world(sm, ledger_owners)
    async with sm() as s:
        await persist_turn(s, w.owner.id, record(w))
    cfg = await _scalar(sm, "SELECT config FROM rag_runs WHERE id = :r", r=w.run_id)
    assert cfg == CONFIG
    assert cfg["prompts"] == ["answer_rag@1"] and cfg["retrieval"] == "retrieval@1"
    assert cfg["models"] == {"balanced": "gpt-6.1-terra"}
    # a config without the required keys is refused
    w2 = await make_world(sm, w.owner, client_message_id="m2")
    async with sm() as s:
        with pytest.raises(LedgerError, match="config"):
            await persist_turn(s, w.owner.id, record(w2, config={"prompts": []}))


async def test_atomic(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    w = await _world(sm, ledger_owners)
    c = await add_call(sm, w.owner.id)

    async def boom(*a: Any, **k: Any) -> None:
        raise RuntimeError("injected after segments")

    monkeypatch.setattr(ledger, "_insert_evidence", boom)
    async with sm() as s:
        with pytest.raises(RuntimeError, match="injected"):
            await persist_turn(s, w.owner.id, record(w, (c,)))
    n = await _counts(sm, w.run_id)
    assert n == {"messages": 1, "segments": 0, "evidence": 0, "versions": 0, "steps": 0, "candidates": 0}
    async with sm() as s:
        run = (
            (
                await s.execute(
                    text("SELECT status, assistant_message_id FROM rag_runs WHERE id = :r"), {"r": w.run_id}
                )
            )
            .mappings()
            .one()
        )
    assert run["status"] == "running" and run["assistant_message_id"] is None
    assert await _scalar(sm, "SELECT run_id FROM llm_calls WHERE id = :c", c=c) is None
    assert (
        await _scalar(sm, "SELECT active_run_id FROM chat_sessions WHERE id = :s", s=w.session_id) == w.run_id
    )
    # and a retry after the fault succeeds
    monkeypatch.undo()
    async with sm() as s:
        assert (await persist_turn(s, w.owner.id, record(w, (c,)))).created


async def test_cross_owner_run_not_found(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any]
) -> None:
    w = await _world(sm, ledger_owners)
    other = await ledger_owners()
    async with sm() as s:
        with pytest.raises(ledger.RunNotFound):
            await persist_turn(s, other.id, record(w))
