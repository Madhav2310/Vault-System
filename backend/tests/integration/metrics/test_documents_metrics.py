"""GET /v1/metrics/documents: hand-computed values, admin-only global scope, owner isolation."""

import datetime as dt
import uuid
from collections.abc import Callable
from typing import Any

import httpx
from sqlalchemy import text

from tests.support.docs import seed_chunk, seed_document

T0 = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def blob(n: int) -> bytes:
    """n unique bytes (distinct sha per call)."""
    return (uuid.uuid4().bytes + bytes(n))[:n]


async def set_version(
    sm: Any, version_id: uuid.UUID, *, pages: int | None = None, ready_after_s: float | None = None
) -> None:
    async with sm() as s:
        await s.execute(
            text(
                "UPDATE document_versions SET page_count = :p, created_at = :c, "
                "ready_at = CAST(:c AS timestamptz) + make_interval(secs => CAST(:r AS float8)) "
                "WHERE id = :v"
            ),
            {"p": pages, "c": T0, "r": ready_after_s, "v": version_id},
        )
        await s.commit()


async def seed_owner_a(sm: Any, owner_id: uuid.UUID) -> None:
    d1 = await seed_document(sm, owner_id, status="ready", content=blob(1000))
    d2 = await seed_document(sm, owner_id, status="enriched", content=blob(2000))
    d3 = await seed_document(sm, owner_id, status="ready", content=blob(500), mime=DOCX)
    d4 = await seed_document(sm, owner_id, status="needs_ocr", content=blob(300))
    await seed_document(sm, owner_id, status="failed", content=blob(200), mime="text/plain")
    d6 = await seed_document(sm, owner_id, status="ready", content=blob(9999))  # soft-deleted below
    await set_version(sm, d1.version_id, pages=10, ready_after_s=10)
    await set_version(sm, d2.version_id, pages=20, ready_after_s=20)
    await set_version(sm, d3.version_id, pages=None, ready_after_s=40)
    await set_version(sm, d4.version_id, pages=5)
    await set_version(sm, d6.version_id, pages=99, ready_after_s=999)
    for i in range(3):
        await seed_chunk(sm, owner_id, d1, ordinal=i)
    for i in range(2):
        await seed_chunk(sm, owner_id, d2, ordinal=i)
    await seed_chunk(sm, owner_id, d6)  # deleted document: excluded
    async with sm() as s:
        await s.execute(text("UPDATE documents SET deleted_at = now() WHERE id = :d"), {"d": d6.document_id})
        # An older version (version_no 0) of d3: only the latest version of a document counts.
        await s.execute(
            text(
                "INSERT INTO document_versions (id, document_id, owner_id, version_no, filename, mime, "
                "size_bytes, sha256, status, page_count, created_at, ready_at) "
                "SELECT gen_random_uuid(), document_id, owner_id, 0, 'old.pdf', 'application/pdf', 7777, "
                "sha256, 'superseded', 77, :c, :c FROM document_versions WHERE id = :v"
            ),
            {"c": T0, "v": d3.version_id},
        )
        await s.commit()


async def test_values(api: httpx.AsyncClient, sm: Any, mk_owner: Callable[..., Any]) -> None:
    a = await mk_owner()
    await seed_owner_a(sm, a.id)
    r = await api.get("/v1/metrics/documents", headers=a.auth)
    assert r.status_code == 200
    body = r.json()
    # Latest version of each non-deleted document: d1 ready pdf, d2 enriched pdf, d3 ready docx,
    # d4 needs_ocr pdf, d5 failed txt  -> 5 documents (d6 deleted; d3's older version ignored).
    assert body["scope"] == "owner"
    assert body["documents"] == 5
    assert body["by_status"] == {
        "uploaded": 0,
        "processing": 0,
        "ready": 2,  # d1, d3
        "enriched": 1,  # d2
        "needs_ocr": 1,  # d4
        "unsupported": 0,
        "failed": 1,  # d5
        "superseded": 0,
    }
    assert body["by_mime"] == {"application/pdf": 3, DOCX: 1, "text/plain": 1}
    assert body["needs_ocr"] == 1
    assert body["pages"] == 35  # 10 + 20 + NULL + 5 + NULL
    assert body["chunks"] == 5  # 3 on d1 + 2 on d2
    assert body["storage_bytes"] == 4000  # 1000 + 2000 + 500 + 300 + 200
    # time-to-ready samples (ready_at - created_at): d1 10 s, d2 20 s, d3 40 s (d4, d5 never ready).
    # p50 = 20 (middle). p95: rank 0.95 * (3 - 1) = 1.9 -> 20 + 0.9 * (40 - 20) = 38.
    assert body["time_to_ready_seconds"] == {"p50": 20.0, "p95": 38.0}


async def test_empty_state(api: httpx.AsyncClient, mk_owner: Callable[..., Any]) -> None:
    a = await mk_owner()
    r = await api.get("/v1/metrics/documents", headers=a.auth)
    assert r.status_code == 200
    body = r.json()
    assert body["documents"] == 0 and body["pages"] == 0 and body["chunks"] == 0
    assert body["storage_bytes"] == 0 and body["by_mime"] == {} and body["needs_ocr"] == 0
    assert set(body["by_status"].values()) == {0}
    assert body["time_to_ready_seconds"] == {"p50": None, "p95": None}


async def test_global_admin_only(api: httpx.AsyncClient, sm: Any, mk_owner: Callable[..., Any]) -> None:
    a = await mk_owner()
    b = await mk_owner()
    admin = await mk_owner(admin=True)
    await seed_owner_a(sm, a.id)
    await seed_document(sm, b.id, content=blob(123456))
    r = await api.get("/v1/metrics/documents?scope=global", headers=a.auth)
    assert r.status_code == 403
    assert r.json()["code"] == "forbidden"
    g = await api.get("/v1/metrics/documents?scope=global", headers=admin.auth)
    assert g.status_code == 200 and g.json()["scope"] == "global"
    # Global includes at least A's 5 documents and B's 1, and their storage (other rows may exist).
    assert g.json()["documents"] >= 6
    assert g.json()["storage_bytes"] >= 4000 + 123456
    # An admin without ?scope=global still sees only their own (empty) data.
    own = await api.get("/v1/metrics/documents", headers=admin.auth)
    assert own.json()["documents"] == 0
    bad = await api.get("/v1/metrics/documents?scope=everything", headers=admin.auth)
    assert bad.status_code == 422


async def test_owner_scoped(api: httpx.AsyncClient, sm: Any, mk_owner: Callable[..., Any]) -> None:
    a = await mk_owner()
    b = await mk_owner()
    await seed_owner_a(sm, a.id)
    sb = await seed_document(sm, b.id, status="ready", content=blob(123456))
    await seed_chunk(sm, b.id, sb)
    await set_version(sm, sb.version_id, pages=3, ready_after_s=1000)
    rb = (await api.get("/v1/metrics/documents", headers=b.auth)).json()
    assert rb["documents"] == 1 and rb["storage_bytes"] == 123456 and rb["chunks"] == 1
    assert rb["pages"] == 3 and rb["by_status"]["ready"] == 1
    assert rb["time_to_ready_seconds"] == {"p50": 1000.0, "p95": 1000.0}
    ra = (await api.get("/v1/metrics/documents", headers=a.auth)).json()
    assert ra["documents"] == 5 and ra["storage_bytes"] == 4000 and ra["chunks"] == 5
    assert (await api.get("/v1/metrics/documents")).status_code == 401
