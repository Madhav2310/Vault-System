"""Support check inside the real turn graph over the database (fake provider, mocked reranker)."""

import json
import uuid
from collections.abc import Callable
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import get_turn_runner
from app.chat.turn import TurnDeps, run_turn
from app.core.ids import uuid7
from app.ingestion.sentences import split_sentences
from app.llm.gateway import DbLedgerWriter
from app.llm.providers.fake import Step, refusal, structured
from app.retrieval.query_embed import QueryEmbedder
from app.retrieval.retrieve import Retriever
from tests.support.chat_gen import fake_gateway
from tests.support.docs import seed_document
from tests.support.ledger_env import ledger_owners

__all__ = ["ledger_owners"]  # fixture import

BODY = (
    "The Consultant shall provide the Services for twelve months. "
    "Either party may terminate on 30 days notice."
)
Q = "Services twelve months"
EMBED_SLOT = structured({})  # the fake provider's embed() pops one scripted step


def _analysis() -> Any:
    return structured(
        {
            "standalone_query": Q,
            "target_refs": [],
            "dimensions": [],
            "intent": "qa",
            "needs_whole_document": False,
            "clarification": None,
        }
    )


def _answer(*segments: tuple[str, list[str]]) -> Any:
    return structured({"status": "answer", "segments": [{"text": t, "evidence": e} for t, e in segments]})


def _verdicts(*items: tuple[int, str]) -> Any:
    return structured({"verdicts": [{"index": i, "verdict": v, "reason": f"{v} reason"} for i, v in items]})


async def _seed_chunk(sm: async_sessionmaker[AsyncSession], owner_id: uuid.UUID) -> uuid.UUID:
    doc = await seed_document(sm, owner_id, title="Services Agreement")
    cid = uuid7()
    sents = [list(se) for se in split_sentences(BODY)]
    async with sm() as s:
        await s.execute(
            text(
                "INSERT INTO chunks (id, owner_id, document_id, version_id, ordinal, kind, original_text, "
                "embedding_text, page_start, page_end, section_path, sentences, spans, token_count, "
                "content_hash) VALUES (:id, :o, :d, :v, 0, 'text', :t, :t, 3, 3, '{Term}', "
                "CAST(:sent AS jsonb), CAST('[]' AS jsonb), 20, 'h')"
            ),
            {"id": cid, "o": owner_id, "d": doc.document_id, "v": doc.version_id, "t": BODY,
             "sent": json.dumps(sents)},
        )  # fmt: skip
        await s.commit()
    return cid


async def _turn(
    sm: async_sessionmaker[AsyncSession], owners: Callable[[], Any], api: httpx.AsyncClient, *script: Step
) -> tuple[Any, uuid.UUID, Any, dict[str, Any]]:
    """Run one turn with the scripted provider; returns (provider, session id, owner, POST response body)."""
    o = await owners()
    await _seed_chunk(sm, o.id)
    gw, prov = fake_gateway([_analysis(), EMBED_SLOT, *script], ledger=DbLedgerWriter(sm))
    deps = TurnDeps(sm, gw, Retriever(QueryEmbedder(gw, None), None))

    async def runner(*a: Any) -> None:
        await run_turn(deps, *a)

    api._transport.app.dependency_overrides[get_turn_runner] = lambda: runner  # type: ignore[attr-defined]
    sid = uuid.UUID((await api.post("/v1/chat/sessions", json={}, headers=o.auth)).json()["id"])
    r = await api.post(
        f"/v1/chat/sessions/{sid}/messages", json={"content": Q, "client_message_id": "m1"}, headers=o.auth
    )
    assert r.status_code == 200, r.text
    return prov, sid, o, r.json()


async def _history(api: httpx.AsyncClient, sid: uuid.UUID, o: Any) -> list[dict[str, Any]]:
    h = (await api.get(f"/v1/chat/sessions/{sid}/messages", headers=o.auth)).json()["items"]
    segments: list[dict[str, Any]] = h[1]["segments"]
    return segments


async def _reasons(sm: async_sessionmaker[AsyncSession], o: Any) -> tuple[list[str], int]:
    async with sm() as s:
        row = (
            await s.execute(
                text("SELECT reason_codes, llm_calls_used FROM rag_runs WHERE owner_id = :o"), {"o": o.id}
            )
        ).one()
    return list(row[0]), row[1]


async def test_verdicts_persisted(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    prov, sid, o, body = await _turn(
        sm,
        ledger_owners,
        api,
        _answer(("Twelve months.", ["E1.0"]), ("Thirty days.", ["E1.1"]), ("Unknown extra.", [])),
        _verdicts((0, "supported"), (1, "partial")),
    )
    assert body["message"]["response_type"] == "answer"
    segs = await _history(api, sid, o)
    assert [(s["support"], s["verification"]) for s in segs] == [
        ("cited", "supported"),
        ("cited", "partial"),
        ("none", "unchecked"),
    ]
    assert segs[0]["verification_reason"] == "supported reason" and segs[2]["verification_reason"] is None
    check_req = str(prov.requests[-1])
    assert "Unknown extra." not in check_req  # the model never sees uncited text
    assert "The Consultant shall provide" in check_req and "Either party may terminate" in check_req
    reasons, calls_used = await _reasons(sm, o)
    assert "UNSUPPORTED_CLAIM" not in reasons and "SUPPORT_CHECK_FAILED" not in reasons and calls_used == 3
    async with sm() as s:  # its cost is on the run's ledger
        n = (
            await s.execute(
                text(
                    "SELECT count(*) FROM llm_calls "
                    "WHERE owner_id = :o AND purpose = 'support_check' AND run_id IS NOT NULL"
                ),
                {"o": o.id},
            )
        ).scalar_one()
    assert n == 1


async def test_failure_never_blocks(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    _, sid, o, body = await _turn(sm, ledger_owners, api, _answer(("Twelve months.", ["E1.0"])), refusal())
    assert body["message"]["response_type"] == "answer" and body["message"]["content"] == "Twelve months."
    segs = await _history(api, sid, o)
    assert [s["verification"] for s in segs] == ["unchecked"]
    reasons, _ = await _reasons(sm, o)
    assert "SUPPORT_CHECK_FAILED" in reasons and "UNSUPPORTED_CLAIM" not in reasons


async def test_unsupported_reason(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    _, sid, o, _ = await _turn(
        sm,
        ledger_owners,
        api,
        _answer(("Twelve months.", ["E1.0"]), ("Ninety days.", ["E1.1"])),
        _verdicts((0, "supported"), (1, "unsupported")),
    )
    segs = await _history(api, sid, o)
    assert [s["verification"] for s in segs] == ["supported", "unsupported"]
    reasons, _ = await _reasons(sm, o)
    assert "UNSUPPORTED_CLAIM" in reasons


async def test_skipped_without_citations(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    prov, sid, o, body = await _turn(sm, ledger_owners, api, _answer(("Just my opinion.", [])))
    assert body["message"]["response_type"] == "answer"
    assert len(prov.requests) == 2  # analyze + generate only: no support-check call
    segs = await _history(api, sid, o)
    assert [(s["support"], s["verification"]) for s in segs] == [("none", None)]
    async with sm() as s:
        steps = (
            (
                await s.execute(
                    text(
                        "SELECT node FROM run_steps st JOIN rag_runs r ON r.id = st.run_id "
                        "WHERE r.owner_id = :o"
                    ),
                    {"o": o.id},
                )
            )
            .scalars()
            .all()
        )
    assert "verify" not in steps
