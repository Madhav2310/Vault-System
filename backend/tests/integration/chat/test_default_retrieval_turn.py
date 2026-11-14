"""The default retrieval config (LLM listwise rerank) in a real turn: the rerank call is a scripted step."""

import uuid
from collections.abc import Callable
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import get_turn_runner
from app.chat.turn import TurnDeps, run_turn
from app.core.settings import Settings
from app.llm.gateway import DbLedgerWriter
from app.llm.providers.fake import structured
from app.retrieval.config import get_config
from app.retrieval.query_embed import QueryEmbedder
from app.retrieval.retrieve import Retriever
from tests.integration.chat.test_turn_e2e import EMBED_SLOT, _post, _rerank, _seed_chunk
from tests.support.chat_gen import fake_gateway
from tests.support.ledger_env import ledger_owners

__all__ = ["ledger_owners"]


def test_the_shipped_default_is_the_llm_reranker() -> None:
    default = Settings.model_fields["retrieval_config"].default
    cfg = get_config(default)
    assert cfg.rerank.kind == "llm" and cfg.rerank.candidates == 50 and cfg.rerank.top_n == 8


async def test_turn_with_llm_rerank_answers_and_records_the_rerank_call(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    o = await ledger_owners()
    await _seed_chunk(sm, o.id, "Services Agreement")
    q = "Services twelve months"
    gw, _ = fake_gateway(
        [
            EMBED_SLOT,
            structured({"ranking": [1]}),
            structured({"status": "answer", "segments": [{"text": "Twelve months.", "evidence": ["E1.0"]}]}),
            structured({"verdicts": [{"index": 0, "verdict": "supported", "reason": "same fact"}]}),
        ],
        ledger=DbLedgerWriter(sm),
        fast_path=True,
    )
    cfg = get_config(Settings.model_fields["retrieval_config"].default)
    deps = TurnDeps(sm, gw, Retriever(QueryEmbedder(gw, None), _rerank(), None, cfg))

    async def runner(*a: Any) -> None:
        await run_turn(deps, *a)

    api._transport.app.dependency_overrides[get_turn_runner] = lambda: runner  # type: ignore[attr-defined]
    sid = uuid.UUID((await api.post("/v1/chat/sessions", json={}, headers=o.auth)).json()["id"])
    r = await _post(api, o, sid, q, "m1")
    assert r.status_code == 200, r.text
    msg = r.json()["message"]
    assert msg["response_type"] == "answer" and msg["content"] == "Twelve months."
    async with sm() as s:
        retrieval = (
            await s.execute(
                text("SELECT config->>'retrieval' FROM rag_runs WHERE owner_id = :o"), {"o": o.id}
            )
        ).scalar_one()
        purposes = (
            (
                await s.execute(
                    text("SELECT purpose FROM llm_calls WHERE owner_id = :o ORDER BY created_at"), {"o": o.id}
                )
            )
            .scalars()
            .all()
        )
    assert retrieval == cfg.version and "rerank" in purposes
