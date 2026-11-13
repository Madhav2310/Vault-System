"""S3.01 chat session endpoints."""

import uuid
from collections.abc import Callable
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.support.docs import seed_document


async def _create(api: httpx.AsyncClient, owner: Any, **body: Any) -> httpx.Response:
    return await api.post("/v1/chat/sessions", json=body, headers=owner.auth)


async def test_create_with_scope(
    api: httpx.AsyncClient, owners: Callable[[], Any], sm: async_sessionmaker[AsyncSession]
) -> None:
    o = await owners()
    d1, d2 = await seed_document(sm, o.id), await seed_document(sm, o.id)
    r = await _create(api, o, document_ids=[str(d1.document_id), str(d2.document_id)], title="T")
    assert r.status_code == 201, r.text
    body = r.json()
    assert sorted(body["document_ids"]) == sorted([str(d1.document_id), str(d2.document_id)])
    assert body["title"] == "T"
    got = await api.get(f"/v1/chat/sessions/{body['id']}", headers=o.auth)
    assert got.status_code == 200 and got.json()["document_ids"] == body["document_ids"]
    async with sm() as s:
        stored = (
            await s.execute(
                text("SELECT scope_document_ids FROM chat_sessions WHERE id = :i"), {"i": body["id"]}
            )
        ).scalar_one()
    assert set(stored) == {d1.document_id, d2.document_id}


async def test_create_all_scope(
    api: httpx.AsyncClient, owners: Callable[[], Any], sm: async_sessionmaker[AsyncSession]
) -> None:
    o = await owners()
    for body in ({}, {"document_ids": []}, {"document_ids": None}):
        r = await _create(api, o, **body)
        assert r.status_code == 201, r.text
        assert r.json()["document_ids"] is None
        async with sm() as s:
            stored = (
                await s.execute(
                    text("SELECT scope_document_ids IS NULL FROM chat_sessions WHERE id = :i"),
                    {"i": r.json()["id"]},
                )
            ).scalar_one()
        assert stored is True


async def test_foreign_doc_404(
    api: httpx.AsyncClient, owners: Callable[[], Any], sm: async_sessionmaker[AsyncSession]
) -> None:
    a, b = await owners(), await owners()
    mine, theirs = await seed_document(sm, a.id), await seed_document(sm, b.id)
    r = await _create(api, a, document_ids=[str(mine.document_id), str(theirs.document_id)])
    assert r.status_code == 404 and r.json()["code"] == "document_not_found"
    r = await _create(api, a, document_ids=[str(uuid.uuid4())])
    assert r.status_code == 404 and r.json()["code"] == "document_not_found"
    # a deleted document is not a valid scope either
    async with sm() as s:
        await s.execute(
            text("UPDATE documents SET deleted_at = now() WHERE id = :d"), {"d": mine.document_id}
        )
        await s.commit()
    r = await _create(api, a, document_ids=[str(mine.document_id)])
    assert r.status_code == 404 and r.json()["code"] == "document_not_found"
    r = await api.get("/v1/chat/sessions", headers=a.auth)
    assert r.json()["items"] == []  # nothing was created by the failed attempts


async def test_cross_owner(api: httpx.AsyncClient, owners: Callable[[], Any]) -> None:
    a, b = await owners(), await owners()
    sid = (await _create(api, a, title="mine")).json()["id"]
    assert (await api.get(f"/v1/chat/sessions/{sid}", headers=b.auth)).status_code == 404
    assert (await api.get("/v1/chat/sessions", headers=b.auth)).json()["items"] == []
    assert [i["id"] for i in (await api.get("/v1/chat/sessions", headers=a.auth)).json()["items"]] == [sid]
    r = await api.post(
        f"/v1/chat/sessions/{sid}/messages",
        json={"content": "hi", "client_message_id": "c1"},
        headers=b.auth,
    )
    assert r.status_code == 404
    assert (await api.get(f"/v1/chat/sessions/{uuid.uuid4()}", headers=a.auth)).status_code == 404


async def test_list_cursor(api: httpx.AsyncClient, owners: Callable[[], Any]) -> None:
    o = await owners()
    ids = [(await _create(api, o, title=str(i))).json()["id"] for i in range(5)]
    seen: list[str] = []
    cursor: str | None = None
    while True:
        params: dict[str, Any] = {"limit": 2}
        if cursor:
            params["cursor"] = cursor
        body = (await api.get("/v1/chat/sessions", params=params, headers=o.auth)).json()
        seen += [i["id"] for i in body["items"]]
        cursor = body["next_cursor"]
        if not cursor:
            break
    assert seen == ids[::-1]
    assert (await api.get("/v1/chat/sessions?cursor=zzz", headers=o.auth)).status_code == 422


async def test_requires_auth(api: httpx.AsyncClient) -> None:
    assert (await api.get("/v1/chat/sessions")).status_code == 401
