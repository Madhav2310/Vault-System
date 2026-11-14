"""Idempotency-Key on resource-creating POSTs (exercised through POST /v1/documents)."""

import asyncio

import httpx
from sqlalchemy import text

from tests.support.uploads import VALID_PDF, docx_bytes, multipart


async def _post(api, owner, key: str, data: bytes, name: str = "a.pdf"):  # type: ignore[no-untyped-def]
    body, h = multipart({}, (name, data))
    return await api.post("/v1/documents", content=body, headers={**h, **owner.auth, "Idempotency-Key": key})


async def _docs(sm, owner) -> int:  # type: ignore[no-untyped-def]
    async with sm() as s:
        return (
            await s.execute(text("SELECT count(*) FROM documents WHERE owner_id = :o"), {"o": owner.id})
        ).scalar_one()


async def test_replay_same_response(api: httpx.AsyncClient, make_owner, sm) -> None:  # type: ignore[no-untyped-def]
    o = await make_owner()
    first = await _post(api, o, "k-1", VALID_PDF.read_bytes())
    again = await _post(api, o, "k-1", VALID_PDF.read_bytes())
    assert first.status_code == again.status_code == 202  # replayed, not a 200 dedupe
    assert first.json() == again.json()
    assert await _docs(sm, o) == 1


async def test_key_reuse_different_body(api: httpx.AsyncClient, make_owner) -> None:  # type: ignore[no-untyped-def]
    o = await make_owner()
    assert (await _post(api, o, "k-2", VALID_PDF.read_bytes())).status_code == 202
    r = await _post(api, o, "k-2", docx_bytes("different"), name="b.docx")
    assert r.status_code == 422 and r.json()["code"] == "idempotency_key_reused"


async def test_keys_scoped_by_owner(api: httpx.AsyncClient, make_owner, sm) -> None:  # type: ignore[no-untyped-def]
    a, b = await make_owner(), await make_owner()
    ra = await _post(api, a, "shared-key", VALID_PDF.read_bytes())
    rb = await _post(api, b, "shared-key", VALID_PDF.read_bytes())
    assert ra.status_code == rb.status_code == 202
    assert ra.json()["document_id"] != rb.json()["document_id"]
    assert await _docs(sm, a) == await _docs(sm, b) == 1


async def test_concurrent_same_key(api: httpx.AsyncClient, make_owner, sm) -> None:  # type: ignore[no-untyped-def]
    o = await make_owner()
    data = docx_bytes("concurrent idempotency")
    rs = await asyncio.gather(*(_post(api, o, "k-c", data, "c.docx") for _ in range(5)))
    assert {r.status_code for r in rs} == {202}
    assert len({r.text for r in rs}) == 1  # every caller sees the same response body
    assert await _docs(sm, o) == 1
