"""Speculative retrieval: the raw question is retrieved while `analyze` runs; the result is used only when the
analysis leaves query and scope unchanged. Same results as sequential, at most one extra embedding when
discarded, and less wall time (fake latency)."""

import asyncio
import time
import uuid
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.llm.providers.fake import FakeProvider, Step, structured
from app.retrieval.retrieve import Retriever
from tests.support.ledger_env import ledger_owners
from tests.support.turn_env import (
    EMBED_SLOT,
    analysis,
    answer,
    answer_script,
    install_runner,
    new_session,
    post,
    seed_chunk,
)

__all__ = ["ledger_owners"]  # fixture import

Q = "Services twelve months"
ONE = (("Twelve months.", ["E1.0"]),)
RETRIEVE_S, CHAT_S, EMBED_S = 0.3, 0.5, 0.05
VERDICT = structured({"verdicts": [{"index": 0, "verdict": "supported", "reason": "ok"}]})


async def retrieve_step(sm: async_sessionmaker[AsyncSession], owner: uuid.UUID) -> dict[str, Any]:
    async with sm() as s:
        rows = await s.execute(
            text(
                "SELECT st.decision FROM run_steps st JOIN rag_runs r ON r.id = st.run_id "
                "WHERE r.owner_id = :o AND st.node = 'retrieve' ORDER BY r.created_at DESC LIMIT 1"
            ),
            {"o": owner},
        )
        return dict(rows.scalar_one())


async def candidates(sm: async_sessionmaker[AsyncSession], owner: uuid.UUID) -> list[Any]:
    async with sm() as s:
        rows = await s.execute(
            text(
                "SELECT c.chunk_id, c.rrf_score, c.selected FROM retrieval_candidates c "
                "JOIN rag_runs r ON r.id = c.run_id WHERE r.owner_id = :o AND r.created_at = "
                "(SELECT max(created_at) FROM rag_runs WHERE owner_id = :o) ORDER BY c.chunk_id"
            ),
            {"o": owner},
        )
        return list(rows)


def slow_world(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retrieval takes RETRIEVE_S, chat calls CHAT_S, the embedding EMBED_S (as in production: the embedding
    is quicker than the analysis, so the retrieval can finish inside the analysis call)."""
    orig = Retriever.retrieve

    async def slow(self: Retriever, *a: Any, **k: Any) -> Any:
        await asyncio.sleep(RETRIEVE_S)
        return await orig(self, *a, **k)

    monkeypatch.setattr(Retriever, "retrieve", slow)
    quick_embed(monkeypatch)


def quick_embed(monkeypatch: pytest.MonkeyPatch) -> None:
    orig_embed = FakeProvider.embed

    async def quick(self: FakeProvider, *a: Any, **k: Any) -> Any:
        lat, self.latency_s = self.latency_s, EMBED_S
        try:
            return await orig_embed(self, *a, **k)
        finally:
            self.latency_s = lat

    monkeypatch.setattr(FakeProvider, "embed", quick)


async def run_once(
    api: httpx.AsyncClient,
    sm: async_sessionmaker[AsyncSession],
    o: Any,
    script: list[Step],
    speculative: bool,
    latency: float = 0.0,
) -> tuple[float, FakeProvider]:
    prov = install_runner(api, sm, script, chat_speculative_retrieval=speculative)
    prov.latency_s = latency
    sid = await new_session(api, o)
    t0 = time.monotonic()
    r = await post(api, o, sid, Q, uuid.uuid4().hex)
    assert r.status_code == 200 and r.json()["message"]["response_type"] == "answer"
    return time.monotonic() - t0, prov


def leftovers() -> list[Any]:
    return [t for t in asyncio.all_tasks() if "retrieve_raw" in repr(t.get_coro())]


async def test_hit_is_same_as_sequential_and_faster(
    sm: async_sessionmaker[AsyncSession],
    ledger_owners: Callable[[], Any],
    api: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    o = await ledger_owners()
    await seed_chunk(sm, o.id)
    slow_world(monkeypatch)
    off_s, _ = await run_once(api, sm, o, answer_script(Q, *ONE), False, CHAT_S)
    off_c = await candidates(sm, o.id)
    assert (await retrieve_step(sm, o.id))["speculative"] == "none"
    on_s, prov = await run_once(api, sm, o, answer_script(Q, *ONE), True, CHAT_S)
    on_c = await candidates(sm, o.id)
    step = await retrieve_step(sm, o.id)
    assert step["speculative"] == "hit" and step["embed_reused"] is True
    assert on_c == off_c and on_c  # same candidates, scores and selection as the sequential run
    assert len(prov.requests) == 3 and len(prov.embed_calls) == 1  # analyze + generate + support; one embed
    print(f"\nspeculative retrieval: sequential {off_s:.2f}s, speculative {on_s:.2f}s")
    assert off_s - on_s > RETRIEVE_S * 0.6, (off_s, on_s)  # the retrieval time is hidden by the analysis


async def test_miss_discards_and_costs_one_embedding(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    o = await ledger_owners()
    await seed_chunk(sm, o.id)
    rewritten = "Consultant services duration"  # the analysis rewrites the query: the speculation is dropped
    script = [analysis(rewritten), EMBED_SLOT, EMBED_SLOT, answer(*ONE), VERDICT]
    _, prov = await run_once(api, sm, o, script, True)
    step = await retrieve_step(sm, o.id)
    assert step["speculative"] == "miss" and step["embed_reused"] is False
    assert len(prov.embed_calls) == 2  # the raw question (discarded) and the standalone query
    assert len(prov.requests) == 3  # no chat call was added by the speculation
    assert not leftovers()


async def ttft(sm: async_sessionmaker[AsyncSession], owner: uuid.UUID) -> int:
    async with sm() as s:
        rows = await s.execute(
            text("SELECT ttft_ms FROM rag_runs WHERE owner_id = :o ORDER BY created_at DESC LIMIT 1"),
            {"o": owner},
        )
        return int(rows.scalar_one())


async def test_cross_encoder_rerank_hidden_in_analysis(
    sm: async_sessionmaker[AsyncSession],
    ledger_owners: Callable[[], Any],
    api: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With kind=cross_encoder the speculative run includes the rerank, so its time is hidden by the analysis
    and the first segment arrives sooner (TTFT)."""
    from app.retrieval.config import get_config
    from app.retrieval.rerank import RerankClient

    o = await ledger_owners()
    await seed_chunk(sm, o.id)
    orig_rerank = RerankClient.rerank

    async def slow_rerank(self: RerankClient, *a: Any, **k: Any) -> Any:
        await asyncio.sleep(0.4)
        return await orig_rerank(self, *a, **k)

    monkeypatch.setattr(RerankClient, "rerank", slow_rerank)
    quick_embed(monkeypatch)
    ce = get_config("retrieval@2").model_copy(
        update={"rerank": get_config("retrieval@2").rerank.model_copy(update={"kind": "cross_encoder"})}
    )
    out: dict[bool, tuple[float, int]] = {}
    for spec in (False, True):
        prov = install_runner(api, sm, answer_script(Q, *ONE), cfg=ce, chat_speculative_retrieval=spec)
        prov.latency_s = CHAT_S
        sid = await new_session(api, o)
        t0 = time.monotonic()
        r = await post(api, o, sid, Q, uuid.uuid4().hex)
        assert r.json()["message"]["response_type"] == "answer"
        out[spec] = (time.monotonic() - t0, await ttft(sm, o.id))
    print(f"\nrerank 0.4s, chat 0.5s: TTFT sequential {out[False][1]} ms, speculative {out[True][1]} ms")
    assert out[False][1] - out[True][1] > 300  # the 0.4 s rerank (and the search) no longer precedes generate


async def test_cancel_all_stops_leftover_tasks() -> None:
    """`persist` calls this: a turn that never retrieved (meta, failure) leaves no running task."""
    from app.chat.graph import TurnState
    from app.chat.speculative import SpeculativeRetrieval, cancel_all
    from app.retrieval.scope import Scope

    state = TurnState(question="q")
    t1 = asyncio.create_task(asyncio.sleep(60))
    state.spec_retrieval = SpeculativeRetrieval(t1, "q", Scope.empty)  # type: ignore[arg-type]
    await cancel_all(state)
    assert t1.cancelled()
    assert state.spec_retrieval is None
