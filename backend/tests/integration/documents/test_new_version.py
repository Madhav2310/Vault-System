"""POST /v1/documents/{id}/versions: a new version of an existing document."""

import asyncio
import uuid
from collections.abc import Callable
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ingestion.embed_index import IndexStage
from app.ingestion.runner import run_job
from app.retrieval.scope import compute_scope
from tests.support.embed import add_chunks
from tests.support.jobs import JobRef
from tests.support.uploads import docx_bytes, multipart


async def _post_version(
    api: httpx.AsyncClient, owner: Any, document_id: Any, data: bytes, key: str | None = None
) -> httpx.Response:
    body, h = multipart({}, ("contract-v.docx", data))
    headers = {**h, **owner.auth, **({"Idempotency-Key": key} if key else {})}
    return await api.post(f"/v1/documents/{document_id}/versions", content=body, headers=headers)


async def _upload(api: httpx.AsyncClient, owner: Any, data: bytes) -> dict[str, Any]:
    body, h = multipart({}, ("contract.docx", data))
    r = await api.post("/v1/documents", content=body, headers={**h, **owner.auth})
    assert r.status_code == 202, r.text
    out: dict[str, Any] = r.json()
    return out


async def _make_ready(
    sm: async_sessionmaker[AsyncSession], store: Any, enqueued: Any, owner: Any, up: dict[str, Any], tag: str
) -> None:
    """Fast-forward a freshly uploaded version to the index stage (with embedded chunks) and run it."""
    job_id, vid, did = (uuid.UUID(up[k]) for k in ("job_id", "version_id", "document_id"))
    async with sm() as s:
        await s.execute(
            text("UPDATE processing_jobs SET current_stage = 'index' WHERE id = :j"), {"j": job_id}
        )
        await s.commit()
    await add_chunks(sm, owner.id, JobRef(job_id, vid, did), 2, tag=tag, embedded=True)
    from app.core.settings import get_settings

    out = await run_job(
        sm,
        job_id,
        "w",
        {"index": IndexStage()},
        enqueued,
        order=("index",),
        services={"store": store, "settings": get_settings()},
    )
    assert out == "finished"


async def _count_versions(sm: async_sessionmaker[AsyncSession], document_id: Any) -> int:
    async with sm() as s:
        return int(
            (
                await s.execute(
                    text("SELECT count(*) FROM document_versions WHERE document_id = :d"), {"d": document_id}
                )
            ).scalar_one()
        )


async def test_creates_v2(
    api: httpx.AsyncClient, make_owner: Callable[[], Any], enqueued: Any, sm: Any
) -> None:
    o = await make_owner()
    v1 = await _upload(api, o, docx_bytes("first text"))
    enqueued.calls.clear()
    r = await _post_version(api, o, v1["document_id"], docx_bytes("second text"))
    assert r.status_code == 202, r.text
    j = r.json()
    assert j["document_id"] == v1["document_id"] and j["version_id"] != v1["version_id"]
    assert j["status"] == "uploaded" and j["deduplicated"] is False
    assert j["links"]["processing"] == f"/v1/versions/{j['version_id']}/processing"
    assert enqueued.calls == [("extract", uuid.UUID(j["job_id"]))]
    doc = (await api.get(f"/v1/documents/{v1['document_id']}", headers=o.auth)).json()
    assert [(v["version_no"], v["status"]) for v in doc["versions"]] == [(2, "uploaded"), (1, "uploaded")]
    assert doc["versions"][0]["id"] == j["version_id"]


async def test_current_moves(
    api: httpx.AsyncClient, make_owner: Callable[[], Any], enqueued: Any, sm: Any, store: Any
) -> None:
    o = await make_owner()
    v1 = await _upload(api, o, docx_bytes("first text"))
    await _make_ready(sm, store, enqueued, o, v1, "a")
    doc = (await api.get(f"/v1/documents/{v1['document_id']}", headers=o.auth)).json()
    assert doc["current_version_id"] == v1["version_id"] and doc["status"] == "ready"

    v2 = (await _post_version(api, o, v1["document_id"], docx_bytes("second text"))).json()
    doc = (await api.get(f"/v1/documents/{v1['document_id']}", headers=o.auth)).json()
    assert doc["current_version_id"] == v1["version_id"]  # unchanged until v2 is ready

    await _make_ready(sm, store, enqueued, o, v2, "b")
    doc = (await api.get(f"/v1/documents/{v1['document_id']}", headers=o.auth)).json()
    assert doc["current_version_id"] == v2["version_id"] and doc["status"] == "ready"
    by_no = {v["version_no"]: v["status"] for v in doc["versions"]}
    assert by_no == {1: "superseded", 2: "ready"}


async def test_older_version_finishing_last_stays_behind_the_newer(
    api: httpx.AsyncClient, make_owner: Callable[[], Any], enqueued: Any, sm: Any, store: Any
) -> None:
    """v2 (large, slow) and v3 (a quick fix) process concurrently and v3 is ready first: v2 reaching `ready`
    later must neither become current nor supersede the newer v3."""
    o = await make_owner()
    v1 = await _upload(api, o, docx_bytes("first text"))
    await _make_ready(sm, store, enqueued, o, v1, "a")
    v2 = (await _post_version(api, o, v1["document_id"], docx_bytes("second text"))).json()
    v3 = (await _post_version(api, o, v1["document_id"], docx_bytes("third text"))).json()
    await _make_ready(sm, store, enqueued, o, v3, "c")
    await _make_ready(sm, store, enqueued, o, v2, "b")
    doc = (await api.get(f"/v1/documents/{v1['document_id']}", headers=o.auth)).json()
    assert doc["current_version_id"] == v3["version_id"]
    by_no = {v["version_no"]: v["status"] for v in doc["versions"]}
    assert by_no[3] == "ready" and by_no[1] == "superseded"


async def test_concurrent_identical_new_versions_create_one(
    api: httpx.AsyncClient, make_owner: Callable[[], Any], enqueued: Any, sm: Any
) -> None:
    o = await make_owner()
    v1 = await _upload(api, o, docx_bytes("first text"))
    data = docx_bytes("second text")
    rs = await asyncio.gather(*(_post_version(api, o, v1["document_id"], data) for _ in range(4)))
    assert sorted(r.status_code for r in rs) == [200, 200, 200, 202]
    assert len({r.json()["version_id"] for r in rs}) == 1
    assert await _count_versions(sm, uuid.UUID(v1["document_id"])) == 2


async def test_dedupe_same_bytes(
    api: httpx.AsyncClient, make_owner: Callable[[], Any], enqueued: Any, sm: Any, store: Any
) -> None:
    o = await make_owner()
    data = docx_bytes("first text")
    v1 = await _upload(api, o, data)
    await _make_ready(sm, store, enqueued, o, v1, "a")
    enqueued.calls.clear()
    r = await _post_version(api, o, v1["document_id"], data)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["deduplicated"] is True and j["version_id"] == v1["version_id"] and j["job_id"] is None
    assert await _count_versions(sm, uuid.UUID(v1["document_id"])) == 1 and enqueued.calls == []

    # the newest (still processing) version dedupes too: a client retry must not stack versions
    second = docx_bytes("second text")  # once: docx embeds a timestamp
    v2 = (await _post_version(api, o, v1["document_id"], second)).json()
    again = await _post_version(api, o, v1["document_id"], second)
    assert again.status_code == 200 and again.json()["version_id"] == v2["version_id"]
    assert await _count_versions(sm, uuid.UUID(v1["document_id"])) == 2

    # bytes of an older version are a legitimate new version (a revert), not a dedupe
    back = await _post_version(api, o, v1["document_id"], data)
    assert back.status_code == 202 and await _count_versions(sm, uuid.UUID(v1["document_id"])) == 3


async def test_not_found_cases(api: httpx.AsyncClient, make_owner: Callable[[], Any], sm: Any) -> None:
    a, b = await make_owner(), await make_owner()
    v1 = await _upload(api, a, docx_bytes("first text"))
    n_before = await _count_versions(sm, uuid.UUID(v1["document_id"]))

    r = await _post_version(api, b, v1["document_id"], docx_bytes("intruder"))  # another owner's document
    assert r.status_code == 404 and r.json()["code"] == "document_not_found"
    r = await _post_version(api, a, uuid.uuid4(), docx_bytes("nothing"))  # unknown id
    assert r.status_code == 404

    assert (await api.delete(f"/v1/documents/{v1['document_id']}", headers=a.auth)).status_code == 204
    r = await _post_version(api, a, v1["document_id"], docx_bytes("after delete"))  # deleted document
    assert r.status_code == 404 and r.json()["code"] == "document_not_found"
    assert await _count_versions(sm, uuid.UUID(v1["document_id"])) == n_before

    # a bad body on a document the caller does not own is still 404, not a validation error
    r = await api.post(
        f"/v1/documents/{v1['document_id']}/versions",
        content=b"x",
        headers={**b.auth, "Content-Type": "text/plain"},
    )
    assert r.status_code == 404


async def test_idempotent(
    api: httpx.AsyncClient, make_owner: Callable[[], Any], enqueued: Any, sm: Any
) -> None:
    o = await make_owner()
    v1 = await _upload(api, o, docx_bytes("first text"))
    enqueued.calls.clear()
    data = docx_bytes("second text")
    r1 = await _post_version(api, o, v1["document_id"], data, key="k-1")
    r2 = await _post_version(api, o, v1["document_id"], data, key="k-1")
    assert r1.status_code == r2.status_code == 202
    assert r1.content == r2.content  # byte-identical replay
    assert await _count_versions(sm, uuid.UUID(v1["document_id"])) == 2
    assert len(enqueued.calls) == 1  # the replay does not dispatch a second job
    r3 = await _post_version(api, o, v1["document_id"], docx_bytes("other"), key="k-1")
    assert r3.status_code == 422 and r3.json()["code"] == "idempotency_key_reused"


async def test_scope_uses_v2(
    api: httpx.AsyncClient, make_owner: Callable[[], Any], enqueued: Any, sm: Any, store: Any
) -> None:
    o = await make_owner()
    v1 = await _upload(api, o, docx_bytes("first text"))
    await _make_ready(sm, store, enqueued, o, v1, "a")
    v2 = (await _post_version(api, o, v1["document_id"], docx_bytes("second text"))).json()
    async with sm() as s:  # while v2 is processing, scope still answers from v1
        scope = await compute_scope(s, o.id)
    assert scope.entries == ((uuid.UUID(v1["document_id"]), uuid.UUID(v1["version_id"])),)

    await _make_ready(sm, store, enqueued, o, v2, "b")
    async with sm() as s:
        scope = await compute_scope(s, o.id)
        assert scope.entries == ((uuid.UUID(v1["document_id"]), uuid.UUID(v2["version_id"])),)
        rows = (
            (
                await s.execute(
                    text(
                        "SELECT DISTINCT version_id FROM chunks WHERE owner_id = :o AND version_id = ANY(:v)"
                    ),
                    {"o": o.id, "v": list(scope.version_ids)},
                )
            )
            .scalars()
            .all()
        )
    assert rows == [uuid.UUID(v2["version_id"])]
