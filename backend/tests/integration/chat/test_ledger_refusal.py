"""A refused turn keeps its usage and call id, so run totals equal the ledger sum [A]."""

from collections.abc import Callable
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.chat import sessions as svc
from app.chat.turn import TurnDeps, run_turn
from app.llm.gateway import DbLedgerWriter
from app.llm.providers.fake import refusal
from app.retrieval.query_embed import QueryEmbedder
from app.retrieval.retrieve import Retriever
from tests.integration.chat.test_turn_e2e import EMBED_SLOT, _analysis, _seed_chunk
from tests.support.chat_gen import fake_gateway
from tests.support.ledger_env import ledger_owners

__all__ = ["ledger_owners"]  # fixture import


async def test_totals_after_refusal(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any]
) -> None:
    o = await ledger_owners()
    await _seed_chunk(sm, o.id, "Doc")
    gw, _ = fake_gateway([_analysis("terminate notice"), EMBED_SLOT, refusal()], ledger=DbLedgerWriter(sm))
    deps = TurnDeps(sm, gw, Retriever(QueryEmbedder(gw, None), None))
    async with sm() as s:
        sid = (await svc.create_session(s, o.id, None, None)).id
    async with sm() as s:
        t = await svc.start_turn(s, o.id, sid, "terminate notice", "m1")
    await run_turn(deps, o.id, sid, t.run_id, t.user_message_id, "terminate notice")

    async with sm() as s:
        run = (
            (
                await s.execute(
                    text(
                        "SELECT status, llm_calls_used, input_tokens, output_tokens, cost_usd "
                        "FROM rag_runs WHERE id = :r"
                    ),
                    {"r": t.run_id},
                )
            )
            .mappings()
            .one()
        )
        ledger = (
            (
                await s.execute(
                    text(
                        "SELECT count(*) AS n, sum(input_tokens) AS i, sum(output_tokens) AS o, "
                        "sum(cost_usd) AS c FROM llm_calls WHERE run_id = :r"
                    ),
                    {"r": t.run_id},
                )
            )
            .mappings()
            .one()
        )
        refused = (
            await s.execute(
                text("SELECT count(*) FROM llm_calls WHERE run_id = :r AND error_type = 'refusal'"),
                {"r": t.run_id},
            )
        ).scalar_one()
    assert refused == 1 and ledger["n"] >= 2
    assert (run["llm_calls_used"], run["input_tokens"], run["output_tokens"]) == (
        ledger["n"],
        ledger["i"],
        ledger["o"],
    )
    assert run["cost_usd"] == ledger["c"] and run["input_tokens"] > 0
