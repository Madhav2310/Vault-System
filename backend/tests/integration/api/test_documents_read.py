"""S1.27 document read endpoints: list, detail, processing timeline, file, layout, chunk."""

import datetime as dt
import hashlib
import json
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from tests.support.docs import (
    Seeded,
    clear_owner_rows,
    put_gz_json,
    seed_chunk,
    seed_document,
    seed_pages,
)

T0 = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)


@pytest.fixture
async def owners(make_owner: Callable[[], Any], sm: Any) -> AsyncIterator[Callable[[], Any]]:
    made: list[uuid.UUID] = []

    async def mk() -> Any:
        o = await make_owner()
        made.append(o.id)
        return o

    yield mk
    await clear_owner_rows(sm, made)


async def _events(sm: Any, job_id: uuid.UUID, rows: list[tuple[str, str, int, int]]) -> None:
    async with sm() as s:
        for i, (stage, status, attempt, ms) in enumerate(rows):
            await s.execute(
                text(
                    "INSERT INTO job_events (id, job_id, stage, status, attempt, duration_ms, detail, "
                    "created_at)"
                    " VALUES (:id, :j, :st, :status, :a, :ms, CAST('{}' AS jsonb), :ca)"
                ),
                {
                    "id": uuid.uuid4(),
                    "j": job_id,
                    "st": stage,
                    "status": status,
                    "a": attempt,
                    "ms": ms,
                    "ca": T0 + dt.timedelta(seconds=i),
                },
            )
        await s.commit()


async def test_cursor_pagination(api: httpx.AsyncClient, owners, sm) -> None:  # type: ignore[no-untyped-def]
    o = await owners()
    ids = []
    for i in range(6):
        s = await seed_document(sm, o.id, title=f"d{i}", created_at=T0 + dt.timedelta(minutes=i))
        ids.append(str(s.document_id))
    seen: list[str] = []
    cursor = None
    pages = 0
    while True:
        params: dict[str, Any] = {"limit": 2}
        if cursor:
            params["cursor"] = cursor
        r = await api.get("/v1/documents", params=params, headers=o.auth)
        assert r.status_code == 200, r.text
        body = r.json()
        seen += [d["id"] for d in body["items"]]
        pages += 1
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert pages == 3
    assert len(seen) == 6 and set(seen) == set(ids)  # no duplicates, none missing
    assert seen == list(reversed(ids))  # newest first


async def test_status_filter(api: httpx.AsyncClient, owners, sm) -> None:  # type: ignore[no-untyped-def]
    o = await owners()
    ready = await seed_document(sm, o.id, title="Quarterly Report", status="ready", tags=["fin"])
    await seed_document(sm, o.id, title="Other", status="failed", category="misc")
    await seed_document(sm, o.id, title="Third", status="processing")
    r = await api.get("/v1/documents", params={"status": "ready"}, headers=o.auth)
    assert [d["id"] for d in r.json()["items"]] == [str(ready.document_id)]
    assert r.json()["items"][0]["status"] == "ready"
    r = await api.get("/v1/documents", params={"status": "failed"}, headers=o.auth)
    assert [d["title"] for d in r.json()["items"]] == ["Other"]
    # q is a case-insensitive title substring; tag and category are exact filters
    r = await api.get("/v1/documents", params={"q": "QUARTERLY"}, headers=o.auth)
    assert [d["title"] for d in r.json()["items"]] == ["Quarterly Report"]
    r = await api.get("/v1/documents", params={"tag": "fin"}, headers=o.auth)
    assert [d["title"] for d in r.json()["items"]] == ["Quarterly Report"]
    r = await api.get("/v1/documents", params={"category": "misc"}, headers=o.auth)
    assert [d["title"] for d in r.json()["items"]] == ["Other"]
    # bad filters and cursors are validation errors
    assert (await api.get("/v1/documents", params={"status": "bogus"}, headers=o.auth)).status_code == 422
    assert (await api.get("/v1/documents", params={"cursor": "!!"}, headers=o.auth)).status_code == 422


async def test_document_detail(api: httpx.AsyncClient, owners, sm) -> None:  # type: ignore[no-untyped-def]
    o = await owners()
    s = await seed_document(sm, o.id, title="X", quality={"score": 0.9})
    r = await api.get(f"/v1/documents/{s.document_id}", headers=o.auth)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == str(s.document_id) and body["status"] == "ready"
    assert body["versions"][0]["id"] == str(s.version_id)
    assert body["versions"][0]["quality"] == {"score": 0.9}


async def test_processing_timeline(api: httpx.AsyncClient, owners, sm) -> None:  # type: ignore[no-untyped-def]
    o = await owners()
    s = await seed_document(sm, o.id, status="processing", quality={"score": 0.5})
    await _events(sm, s.job_id, [("extract", "succeeded", 1, 120), ("quality", "error", 1, 30)])
    await seed_pages(sm, s.version_id, {1: [], 2: ["low_text"]})
    r = await api.get(f"/v1/versions/{s.version_id}/processing", headers=o.auth)
    assert r.status_code == 200, r.text
    body = r.json()
    assert [(e["stage"], e["status"], e["duration_ms"]) for e in body["stages"]] == [
        ("extract", "succeeded", 120),
        ("quality", "error", 30),
    ]
    assert body["status"] == "processing" and body["job"]["current_stage"] == "extract"
    assert [(p["page_no"], p["flags"]) for p in body["pages"]] == [(1, []), (2, ["low_text"])]
    assert body["quality"] == {"score": 0.5}


async def test_file_stream(api: httpx.AsyncClient, owners, sm, store) -> None:  # type: ignore[no-untyped-def]
    o = await owners()
    data = b"%PDF-1.7\n" + b"x" * 300_000
    s = await seed_document(sm, o.id, content=data, store=store)
    r = await api.get(f"/v1/versions/{s.version_id}/file", headers=o.auth)
    assert r.status_code == 200
    assert hashlib.sha256(r.content).hexdigest() == s.sha256
    assert r.headers["content-type"].startswith("application/pdf")
    assert "a.pdf" in r.headers["content-disposition"]
    # blob missing from the store -> 404, not a 500
    s2 = await seed_document(sm, o.id)
    r = await api.get(f"/v1/versions/{s2.version_id}/file", headers=o.auth)
    assert r.status_code == 404


def _layout(s: Seeded) -> dict[str, Any]:
    return {
        "1": {
            "w": 612.0,
            "h": 792.0,
            "glyphs": [[10.0, 20.0, 15.0, 30.0], [16.0, 20.0, 21.0, 30.0]],
            "char_to_glyph": [0, 1],
        }
    }


async def test_layout(api: httpx.AsyncClient, owners, sm, store) -> None:  # type: ignore[no-untyped-def]
    o = await owners()
    s = await seed_document(sm, o.id)
    await put_gz_json(store, f"layouts/{s.version_id}.json.gz", _layout(s))
    r = await api.get(f"/v1/versions/{s.version_id}/pages/1/layout", headers=o.auth)
    assert r.status_code == 200, r.text
    assert r.json() == {
        "version_id": str(s.version_id),
        "page": 1,
        "width": 612.0,
        "height": 792.0,
        "glyphs": [[10.0, 20.0, 15.0, 30.0], [16.0, 20.0, 21.0, 30.0]],
    }


async def test_layout_out_of_range(api: httpx.AsyncClient, owners, sm, store) -> None:  # type: ignore[no-untyped-def]
    o = await owners()
    s = await seed_document(sm, o.id)
    await put_gz_json(store, f"layouts/{s.version_id}.json.gz", _layout(s))
    for n in (2, 0, 99):
        r = await api.get(f"/v1/versions/{s.version_id}/pages/{n}/layout", headers=o.auth)
        assert r.status_code == 404 and r.json()["code"] == "not_found", (n, r.text)
    # no layout file at all: 404 with a clear detail
    s2 = await seed_document(sm, o.id)
    r = await api.get(f"/v1/versions/{s2.version_id}/pages/1/layout", headers=o.auth)
    assert r.status_code == 404 and "layout" in r.json()["detail"]


async def test_chunk(api: httpx.AsyncClient, owners, sm) -> None:  # type: ignore[no-untyped-def]
    o = await owners()
    s = await seed_document(sm, o.id)
    cid = await seed_chunk(sm, o.id, s, page_start=2, page_end=3, section_path=["Intro", "Scope"])
    r = await api.get(f"/v1/chunks/{cid}", headers=o.auth)
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["id"] == str(cid) and b["text"] == "Alpha beta. Gamma delta."
    assert [x["text"] for x in b["sentences"]] == ["Alpha beta.", "Gamma delta."]
    assert (b["page_start"], b["page_end"], b["section_path"]) == (2, 3, ["Intro", "Scope"])
    assert b["version"] == {"id": str(s.version_id), "version_no": 1, "filename": "a.pdf"}
    assert b["document_id"] == str(s.document_id)


async def test_chunk_neighbours(api: httpx.AsyncClient, owners, sm) -> None:  # type: ignore[no-untyped-def]
    o = await owners()
    s = await seed_document(sm, o.id)
    c0, c1, c2 = [await seed_chunk(sm, o.id, s, ordinal=i) for i in range(3)]

    async def get(cid: uuid.UUID) -> dict[str, Any]:
        r = await api.get(f"/v1/chunks/{cid}", headers=o.auth)
        assert r.status_code == 200, r.text
        return r.json()  # type: ignore[no-any-return]

    first, mid, last = await get(c0), await get(c1), await get(c2)
    assert (first["prev_chunk_id"], first["next_chunk_id"]) == (None, str(c1))
    assert (mid["prev_chunk_id"], mid["next_chunk_id"]) == (str(c0), str(c2))
    assert (last["prev_chunk_id"], last["next_chunk_id"]) == (str(c1), None)


@pytest.mark.parametrize(
    "path",
    [
        "/v1/documents/{doc}",
        "/v1/versions/{ver}/processing",
        "/v1/versions/{ver}/file",
        "/v1/versions/{ver}/pages/1/layout",
        "/v1/chunks/{chunk}",
        "LIST",
    ],
)
async def test_cross_owner_404(api: httpx.AsyncClient, owners, sm, store, path: str) -> None:  # type: ignore[no-untyped-def]
    a, b = await owners(), await owners()
    s = await seed_document(sm, a.id, content=b"%PDF-1.7 data", store=store)
    await put_gz_json(store, f"layouts/{s.version_id}.json.gz", _layout(s))
    cid = await seed_chunk(sm, a.id, s)
    if path == "LIST":
        r = await api.get("/v1/documents", headers=b.auth)
        assert r.status_code == 200 and r.json()["items"] == []
        return
    url = path.format(doc=s.document_id, ver=s.version_id, chunk=cid)
    assert (await api.get(url, headers=a.auth)).status_code == 200  # the owner can read it
    r = await api.get(url, headers=b.auth)
    assert r.status_code == 404, r.text
    assert json.loads(r.text)["code"] in ("not_found", "document_not_found")
