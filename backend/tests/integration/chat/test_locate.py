"""S22.10 routed locate loop inside the real turn graph (fake provider, test database, no live calls)."""

import json
import uuid
from collections.abc import Callable
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.chat.nodes.locate import PgDocStore
from app.core.ids import uuid7
from app.ingestion.sentences import split_sentences
from app.llm.providers.fake import Step, structured
from app.retrieval.config import GateCfg, RetrievalConfig, get_config
from app.retrieval.scope import Scope
from tests.support.docs import seed_document
from tests.support.ledger_env import ledger_owners
from tests.support.turn_env import (
    BODY,
    EMBED_SLOT,
    analysis,
    answer,
    install_runner,
    new_session,
    post,
)

__all__ = ["ledger_owners"]  # fixture import

Q = "Services twelve months"
TEXTS = [
    BODY,
    "Fees are paid by wire.",
    "Filler one.",
    "Filler two.",
    "The indemnity cap is one million dollars.",
    "Notices go by email.",
]
SETTINGS: dict[str, Any] = {"chat_locate": True, "chat_locate_min_chunks": 3}


def cfg() -> RetrievalConfig:  # the floor of 5.0 makes the first pass (score 1.0) a gate abstention
    return get_config("retrieval@2-rerank").model_copy(
        update={"gate": GateCfg(high=0.0, floor=5.0, enabled=True)}
    )


def move(action: str, query: str | None = None, chunk: str | None = None, k: int | None = None) -> Step:
    return structured({"action": action, "query": query, "chunk": chunk, "k": k})


SUPPORT = structured({"verdicts": [{"index": 0, "verdict": "supported", "reason": "ok"}]})


async def seed_doc(
    sm: async_sessionmaker[AsyncSession], owner_id: uuid.UUID, texts: list[str]
) -> tuple[Any, list[uuid.UUID]]:
    doc = await seed_document(sm, owner_id, title="Filing")
    ids: list[uuid.UUID] = []
    async with sm() as s:
        for i, body in enumerate(texts):
            cid = uuid7()
            ids.append(cid)
            await s.execute(
                text(
                    "INSERT INTO chunks (id, owner_id, document_id, version_id, ordinal, kind, "
                    "original_text, "
                    "embedding_text, page_start, page_end, section_path, sentences, spans, token_count, "
                    "content_hash) VALUES (:id, :o, :d, :v, :i, 'text', :t, :t, :p, :p, '{Term}', "
                    "CAST(:sent AS jsonb), CAST('[]' AS jsonb), 20, :h)"
                ),
                {"id": cid, "o": owner_id, "d": doc.document_id, "v": doc.version_id, "i": i, "t": body,
                 "p": i + 1, "sent": json.dumps([list(se) for se in split_sentences(body)]), "h": f"h{i}"},
            )  # fmt: skip
        await s.commit()
    return doc, ids


async def run_row(sm: async_sessionmaker[AsyncSession], owner_id: uuid.UUID) -> Any:
    async with sm() as s:
        return (
            await s.execute(
                text("SELECT reason_codes, analysis, limits FROM rag_runs WHERE owner_id = :o"),
                {"o": owner_id},
            )
        ).one()


async def test_bounded(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    """The model never says done; the loop stops at 4 steps, then the normal answer path runs."""
    o = await ledger_owners()
    await seed_doc(sm, o.id, TEXTS)
    script = [
        analysis(Q), EMBED_SLOT,
        move("find", "indemnity"), move("find", "wire"), move("find", "email"), move("find", "dollars"),
        answer(("One million dollars.", ["E1.0"])), SUPPORT,
    ]  # fmt: skip
    prov = install_runner(api, sm, script, cfg=cfg(), **SETTINGS)
    r = await post(api, o, await new_session(api, o), Q, "m1")
    assert r.status_code == 200 and r.json()["message"]["response_type"] == "answer"
    assert len(prov.requests) == 1 + 4 + 2  # analyze, 4 locate steps, generate, support check
    codes, ana, limits = await run_row(sm, o.id)
    log = ana["locate"]
    assert [e["step"] for e in log] == [1, 2, 3, 4] and all(e["action"] == "find" for e in log)
    assert all(set(e) == {"step", "action", "arg_digest", "new_chunks"} for e in log)
    assert "indemnity" not in json.dumps(ana)  # digests only
    assert "LOCATE_USED" in codes and limits["max_llm_calls"] == 4 + 4  # the route budget plus the loop's cap


async def test_locate_open_adds_neighbours_and_clears_gate(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    o = await ledger_owners()
    await seed_doc(sm, o.id, TEXTS)
    script = [
        analysis(Q), EMBED_SLOT,
        move("find", "indemnity"), move("open", None, "C3", 1), move("done"),
        answer(("One million dollars.", ["E1.0"])), SUPPORT,
    ]  # fmt: skip
    install_runner(api, sm, script, cfg=cfg(), **SETTINGS)
    r = await post(api, o, await new_session(api, o), Q, "m1")
    assert r.json()["message"]["response_type"] == "answer"
    _, ana, _ = await run_row(sm, o.id)
    assert [(e["action"], e["new_chunks"]) for e in ana["locate"]] == [("find", 1), ("open", 2), ("done", 0)]


async def test_invalid_action_keeps_the_gate_abstention(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    o = await ledger_owners()
    await seed_doc(sm, o.id, TEXTS)
    outside = str(uuid.uuid4())  # a well-formed chunk id that is not in the document
    prov = install_runner(
        api, sm, [analysis(Q), EMBED_SLOT, move("open", None, outside, 1)], cfg=cfg(), **SETTINGS
    )
    r = await post(api, o, await new_session(api, o), Q, "m1")
    assert r.json()["message"]["response_type"] == "abstain" and len(prov.requests) == 2
    codes, ana, _ = await run_row(sm, o.id)
    assert ana["locate"] == [
        {"step": 1, "action": "invalid", "arg_digest": ana["locate"][0]["arg_digest"], "new_chunks": 0}
    ]
    assert "LOCATE_STOPPED" in codes and "LOCATE_USED" not in codes


async def test_flag_off_and_short_doc_are_unchanged(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    # flag off: the gate abstains exactly as before; no locate step, no model call, no record
    o = await ledger_owners()
    await seed_doc(sm, o.id, TEXTS)
    prov = install_runner(api, sm, [analysis(Q), EMBED_SLOT], cfg=cfg())
    r = await post(api, o, await new_session(api, o), Q, "m1")
    assert r.json()["message"]["response_type"] == "abstain" and len(prov.requests) == 1
    codes, ana, limits = await run_row(sm, o.id)
    assert (
        "locate" not in ana
        and not [c for c in codes if c.startswith("LOCATE")]
        and limits["max_llm_calls"] == 4
    )

    # flag on, document below the length threshold: the node skips with no model call
    o2 = await ledger_owners()
    await seed_doc(sm, o2.id, TEXTS)
    prov2 = install_runner(
        api, sm, [analysis(Q), EMBED_SLOT], cfg=cfg(), chat_locate=True, chat_locate_min_chunks=50
    )
    r2 = await post(api, o2, await new_session(api, o2), Q, "m2")
    assert r2.json()["message"]["response_type"] == "abstain" and len(prov2.requests) == 1
    _, ana2, _ = await run_row(sm, o2.id)
    assert "locate" not in ana2


async def test_store_is_document_scoped_and_neighbours_clip(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any]
) -> None:
    a, b = await ledger_owners(), await ledger_owners()
    doc, ids = await seed_doc(sm, a.id, TEXTS)
    _other, other_ids = await seed_doc(sm, a.id, ["Unrelated."])
    scope = Scope(entries=((doc.document_id, doc.version_id),))
    store = PgDocStore(sm, None, a.id, scope, uuid.uuid4())  # type: ignore[arg-type]
    assert await store.owns(other_ids[0]) is None  # a chunk of another document
    assert await store.owns(ids[1]) is not None
    assert [h.ordinal for h in await store.open(ids[0], 2)] == [0, 1, 2]  # document start
    assert [h.ordinal for h in await store.open(ids[-1], 2)] == [3, 4, 5]  # document end
    assert [h.ordinal for h in await store.find("  INDEMNITY   cap ")] == [4]  # normalised, case-insensitive
    foreign = PgDocStore(sm, None, b.id, scope, uuid.uuid4())  # type: ignore[arg-type]
    assert await foreign.owns(ids[0]) is None and await foreign.find("indemnity") == []
