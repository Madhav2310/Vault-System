"""Living answers: `on_version_ready` marks answers, history reports staleness, `reask_of` re-asks."""

import uuid
from collections.abc import Callable
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.ids import uuid7
from app.insights.staleness import mark_stale_answers, on_version_ready
from tests.integration.chat.conftest import owners
from tests.support.ledger_env import ledger_owners
from tests.support.turn_env import answer_script, install_runner, post, seed_chunk

__all__ = ["ledger_owners", "owners"]  # fixture imports

Q = "Services twelve months"
SCRIPT = answer_script(Q, ("Twelve months.", ["E1.0"]))


async def _doc_of(sm: async_sessionmaker[AsyncSession], chunk_id: uuid.UUID) -> uuid.UUID:
    async with sm() as s:
        return (
            await s.execute(text("SELECT document_id FROM chunks WHERE id = :c"), {"c": chunk_id})
        ).scalar_one()


async def _session(api: httpx.AsyncClient, owner: Any, doc_id: uuid.UUID) -> uuid.UUID:
    r = await api.post("/v1/chat/sessions", json={"document_ids": [str(doc_id)]}, headers=owner.auth)
    return uuid.UUID(r.json()["id"])


async def _answer(api: httpx.AsyncClient, owner: Any, sid: uuid.UUID, cmid: str = "m1") -> dict[str, Any]:
    r = await post(api, owner, sid, Q, cmid)
    assert r.status_code == 200, r.text
    msgs = (await api.get(f"/v1/chat/sessions/{sid}/messages", headers=owner.auth)).json()["items"]
    return next(m for m in reversed(msgs) if m["role"] == "assistant")


async def _publish_v2(
    sm: async_sessionmaker[AsyncSession], owner_id: uuid.UUID, doc_id: uuid.UUID
) -> uuid.UUID:
    """A second ready version with a copy of the chunk, made current; v1 superseded; the hook called."""
    v2 = uuid7()
    async with sm() as s:
        v1 = (
            await s.execute(text("SELECT current_version_id FROM documents WHERE id = :d"), {"d": doc_id})
        ).scalar_one()
        await s.execute(
            text(
                "INSERT INTO document_versions (id, document_id, owner_id, version_no, filename, mime, "
                "size_bytes, sha256, status) SELECT :n, document_id, owner_id, 2, filename, mime, "
                "size_bytes, "
                "sha256, 'ready' FROM document_versions WHERE id = :o"
            ),
            {"n": v2, "o": v1},
        )
        await s.execute(
            text(
                "INSERT INTO chunks (id, owner_id, document_id, version_id, ordinal, kind, original_text, "
                "embedding_text, page_start, page_end, section_path, sentences, spans, token_count, "
                "content_hash) SELECT :c, owner_id, document_id, :n, ordinal, kind, original_text, "
                "embedding_text, page_start, page_end, section_path, sentences, spans, token_count, "
                "content_hash FROM chunks WHERE version_id = :o"
            ),
            {"c": uuid7(), "n": v2, "o": v1},
        )
        await s.execute(
            text("UPDATE document_versions SET status = 'superseded', superseded_at = now() WHERE id = :o"),
            {"o": v1},
        )
        await s.execute(
            text("UPDATE documents SET current_version_id = :n WHERE id = :d"), {"n": v2, "d": doc_id}
        )
        await on_version_ready(s, owner_id, doc_id, v2)
        await s.commit()
    return v2


async def test_marked_stale(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    o = await ledger_owners()
    doc = await _doc_of(sm, await seed_chunk(sm, o.id, "Services Agreement"))
    install_runner(api, sm, list(SCRIPT))
    sid = await _session(api, o, doc)
    fresh = await _answer(api, o, sid)
    assert fresh["staleness"] is None and fresh["segments"][0]["evidence"]
    await _publish_v2(sm, o.id, doc)
    msgs = (await api.get(f"/v1/chat/sessions/{sid}/messages", headers=o.auth)).json()["items"]
    by_role = {m["role"]: m for m in msgs}
    st = by_role["assistant"]["staleness"]
    assert st["stale"] is True and st["newer_version_no"] == 2 and st["since"]
    assert by_role["user"]["staleness"] is None


async def test_unrelated_and_idempotent(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    o = await ledger_owners()
    a = await _doc_of(sm, await seed_chunk(sm, o.id, "A Agreement"))
    b = await _doc_of(sm, await seed_chunk(sm, o.id, "B Agreement"))
    install_runner(api, sm, [*SCRIPT, *SCRIPT])
    sa, sb = await _session(api, o, a), await _session(api, o, b)
    await _answer(api, o, sa)
    await _answer(api, o, sb)
    v2 = await _publish_v2(sm, o.id, a)
    ma = (await api.get(f"/v1/chat/sessions/{sa}/messages", headers=o.auth)).json()["items"][-1]
    mb = (await api.get(f"/v1/chat/sessions/{sb}/messages", headers=o.auth)).json()["items"][-1]
    assert ma["staleness"]["stale"] is True and mb["staleness"] is None  # the unrelated answer is untouched
    async with sm() as s:
        assert await mark_stale_answers(s, o.id, a, v2) == 0  # a second run marks nothing
        await s.commit()
    again = (await api.get(f"/v1/chat/sessions/{sa}/messages", headers=o.auth)).json()["items"][-1]
    assert again["staleness"]["since"] == ma["staleness"]["since"]  # the timestamp is not rewritten


async def test_reask(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    o, other = await ledger_owners(), await ledger_owners()
    doc = await _doc_of(sm, await seed_chunk(sm, o.id, "Services Agreement"))
    prov = install_runner(api, sm, list(SCRIPT))
    sid = await _session(api, o, doc)
    old = await _answer(api, o, sid)
    v2 = await _publish_v2(sm, o.id, doc)
    prov.push(*answer_script(Q, ("Twelve months.", ["E1.0"])))
    r = await api.post(
        f"/v1/chat/sessions/{sid}/messages",
        json={"reask_of": old["id"], "client_message_id": "re1"},
        headers=o.auth,
    )
    assert r.status_code == 200, r.text
    run_id = r.json()["run_id"]
    async with sm() as s:
        q = (
            await s.execute(text("SELECT original_query FROM rag_runs WHERE id = :r"), {"r": run_id})
        ).scalar_one()
        vs = (
            (await s.execute(text("SELECT version_id FROM run_versions WHERE run_id = :r"), {"r": run_id}))
            .scalars()
            .all()
        )
    assert q == Q and list(vs) == [v2]  # the same question, asked on version 2
    # another owner's message (in their own session or in this one) is a 404, as is an unknown id
    other_sid = uuid.UUID((await api.post("/v1/chat/sessions", json={}, headers=other.auth)).json()["id"])
    for who, session, mid in (
        (other, other_sid, old["id"]),
        (o, sid, str(uuid.uuid4())),
        (other, sid, old["id"]),
    ):
        r = await api.post(
            f"/v1/chat/sessions/{session}/messages",
            json={"reask_of": mid, "client_message_id": "x1"},
            headers=who.auth,
        )
        assert r.status_code == 404, (who, r.text)
    both = await api.post(
        f"/v1/chat/sessions/{sid}/messages",
        json={"reask_of": old["id"], "content": "x", "client_message_id": "x2"},
        headers=o.auth,
    )
    assert both.status_code == 422
