"""`kind: llm` rerank in the real turn graph: ledgered, linked to the run, degrades to fusion order."""

import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.llm.providers.fake import bad_request, structured
from app.retrieval.config import get_config
from app.retrieval.query_embed import QueryEmbedder
from app.retrieval.retrieve import Retriever
from app.retrieval.scope import compute_scope
from tests.support.ledger_env import ledger_owners
from tests.support.llm import make_gateway
from tests.support.retrieval import StubReranker
from tests.support.turn_env import answer_script, install_runner, new_session, post, seed_chunk

__all__ = ["ledger_owners"]  # fixture import


@pytest.fixture
async def owners(
    make_owner: Callable[[], Any], sm: async_sessionmaker[AsyncSession]
) -> AsyncIterator[Callable[[], Any]]:
    """As tests/integration/chat/conftest.py (not shared across directories): runs go before the users."""
    made: list[uuid.UUID] = []

    async def mk() -> Any:
        o = await make_owner()
        made.append(o.id)
        return o

    yield mk
    async with sm() as s:
        for stmt in (
            "DELETE FROM rag_runs WHERE owner_id = ANY(:ids)",
            "DELETE FROM chat_messages WHERE owner_id = ANY(:ids)",
            "DELETE FROM chat_sessions WHERE owner_id = ANY(:ids)",
        ):
            await s.execute(text(stmt), {"ids": made})
        await s.commit()


Q = "Services twelve months"
ONE = (("Twelve months.", ["E1.0"]),)


async def _rerank_calls(sm: async_sessionmaker[AsyncSession], owner_id: Any) -> list[Any]:
    async with sm() as s:
        rows = await s.execute(
            text(
                "SELECT c.run_id, r.id, c.role, c.prompt_id, c.prompt_version, c.status FROM llm_calls c "
                "LEFT JOIN rag_runs r ON r.id = c.run_id WHERE c.owner_id = :o AND c.purpose = 'rerank'"
            ),
            {"o": owner_id},
        )
        return list(rows)


async def test_llm_rerank_ledgered(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    o = await ledger_owners()
    await seed_chunk(sm, o.id)
    base = answer_script(Q, *ONE)[1:]  # fast path: embed slot, answer, support check
    script = [base[0], structured({"ranking": [1]}), *base[1:]]
    prov = install_runner(api, sm, script, fast_path=True, cfg=get_config("retrieval@5"))
    r = await post(api, o, await new_session(api, o), Q, "m1")
    assert r.status_code == 200 and r.json()["message"]["response_type"] == "answer"
    assert len(prov.requests) == 3  # rerank, generate, support check (the embedding is counted apart)
    rows = await _rerank_calls(sm, o.id)
    assert len(rows) == 1
    run_id, run_row, role, prompt_id, version, status = rows[0]
    assert run_id is not None and run_id == run_row  # linked to the run
    assert (role, prompt_id, version, status) == ("fast", "rerank_listwise", 1, "ok")


async def test_llm_rerank_failure_degrades(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    o = await ledger_owners()
    await seed_chunk(sm, o.id)
    base = answer_script(Q, *ONE)[1:]
    script = [base[0], bad_request(), *base[1:]]
    install_runner(api, sm, script, fast_path=True, cfg=get_config("retrieval@5"))
    r = await post(api, o, await new_session(api, o), Q, "m1")
    assert r.status_code == 200 and r.json()["message"]["response_type"] == "answer"
    async with sm() as s:
        degraded = (
            await s.execute(text("SELECT degraded FROM rag_runs WHERE owner_id = :o"), {"o": o.id})
        ).scalar_one()
    assert "no_rerank" in list(degraded)


async def test_rerank_none_calls_no_reranker(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], tmp_path: Any
) -> None:
    o = await ledger_owners()
    await seed_chunk(sm, o.id)
    stub = StubReranker()
    gw, _prov, _, _ = make_gateway(tmp_path)
    r = Retriever(QueryEmbedder(gw, None), None, get_config("retrieval@2"), llm_reranker=stub)  # type: ignore[arg-type]
    async with sm() as s:
        scope = await compute_scope(s, o.id, None)
        res = await r.retrieve(s, o.id, scope, Q)
    assert res.selected and not res.degraded  # fusion order: a deliberate config, not a degraded run
    assert stub.sent == [] and _prov.requests == []
