"""S12.05: `GET /v1/documents/{id}/brief` and the list enrichment."""

from typing import Any

import httpx
from sqlalchemy import text

from tests.integration.insights.test_brief_stage import fact, services_facts
from tests.support.brief_kit import brief_gateway, brief_reply
from tests.support.brief_seed import SERVICES, ChunkSpec, Seeded, handle, run_brief, seed_ready_version
from tests.support.docs import seed_document

TOP_KEYS = {
    "status": str,
    "version_id": str,
    "document_type": str,
    "category": str,
    "tags": list,
    "summary": str,
    "suggested_questions": list,
    "facts": list,
    "tone": dict,
    "model": str,
    "created_at": str,
}
FACT_KEYS = {"field": str, "value": str, "status": str, "evidence": list}
EVIDENCE_KEYS = {
    "chunk_id": str,
    "version_no": int,
    "pages": list,
    "section_path": list,
    "sentence_start": int,
    "sentence_end": int,
    "cited_text": str,
}


def check_shape(obj: dict[str, Any], spec: dict[str, type]) -> None:
    assert set(obj) == set(spec), (sorted(obj), sorted(spec))
    for k, t in spec.items():
        assert isinstance(obj[k], t), (k, type(obj[k]))


async def _briefed(sm, owner, enqueued, facts=None, **kw: Any) -> Seeded:  # type: ignore[no-untyped-def]
    seeded = await seed_ready_version(sm, owner.id, [ChunkSpec(SERVICES)])
    gw, _, _ = brief_gateway([brief_reply(facts if facts is not None else services_facts(), **kw)])
    await run_brief(sm, seeded.ref.job_id, gw, enqueued)
    return seeded


async def test_shape(sm, make_owner, enqueued, api: httpx.AsyncClient) -> None:  # type: ignore[no-untyped-def]
    o = await make_owner()
    seeded = await _briefed(sm, o, enqueued)
    r = await api.get(f"/v1/documents/{seeded.ref.document_id}/brief", headers=o.auth)
    assert r.status_code == 200, r.text
    body = r.json()
    check_shape(body, TOP_KEYS)
    assert body["status"] == "ready" and body["version_id"] == str(seeded.ref.version_id)
    assert (body["document_type"], body["category"]) == ("services agreement", "contract")
    assert body["tags"] == ["consulting", "real estate"] and len(body["suggested_questions"]) == 3
    assert len(body["facts"]) == 5
    for f in body["facts"]:
        check_shape(f, FACT_KEYS)
        for e in f["evidence"]:
            check_shape(e, EVIDENCE_KEYS)
    party = next(f for f in body["facts"] if f["field"] == "party")
    (e,) = party["evidence"]
    assert e["chunk_id"] == str(seeded.chunk_ids[0]) and e["version_no"] == 1 and e["pages"] == [1]
    assert e["section_path"] == ["Agreement"] and (e["sentence_start"], e["sentence_end"]) == (0, 1)
    assert e["cited_text"].startswith("This Services Agreement is made on June 1, 2020")
    assert not any("reason" in f for f in body["facts"])


async def test_pending(sm, make_owner, enqueued, api: httpx.AsyncClient) -> None:  # type: ignore[no-untyped-def]
    o = await make_owner()
    seeded = await seed_ready_version(sm, o.id, [ChunkSpec(SERVICES)])  # the stage has not run
    r = await api.get(f"/v1/documents/{seeded.ref.document_id}/brief", headers=o.auth)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "pending" and body["version_id"] == str(seeded.ref.version_id)
    assert body["facts"] == [] and body["tags"] == [] and body["summary"] is None
    assert set(body) == set(TOP_KEYS)
    # a document still uploading (no ready version yet) is pending too, never 404
    up = await seed_document(sm, o.id, status="uploaded")
    r2 = await api.get(f"/v1/documents/{up.document_id}/brief", headers=o.auth)
    assert r2.status_code == 200 and r2.json()["status"] == "pending"


async def test_failed_and_partial_status(sm, make_owner, enqueued, api: httpx.AsyncClient) -> None:  # type: ignore[no-untyped-def]
    from app.llm.providers import fake

    o = await make_owner()
    seeded = await seed_ready_version(sm, o.id, [ChunkSpec(SERVICES)])
    gw, _, _ = brief_gateway([fake.bad_request()])
    await run_brief(sm, seeded.ref.job_id, gw, enqueued)
    r = await api.get(f"/v1/documents/{seeded.ref.document_id}/brief", headers=o.auth)
    assert r.status_code == 200 and r.json()["status"] == "failed" and r.json()["facts"] == []
    async with sm() as s:  # a partial brief (budget cap) reports `partial`
        await s.execute(
            text(
                "UPDATE document_insights SET status = 'done', content = CAST(:c AS jsonb) "
                "WHERE version_id = :v"
            ),
            {
                "c": '{"document_type":"x","category":"other","tags":[],"summary":"s",'
                '"suggested_questions":[],"facts":[],"reasons":["BRIEF_PARTIAL_BUDGET"],"partial":true}',
                "v": seeded.ref.version_id,
            },
        )
        await s.commit()
    r = await api.get(f"/v1/documents/{seeded.ref.document_id}/brief", headers=o.auth)
    assert r.json()["status"] == "partial" and r.json()["summary"] == "s"


async def test_cross_owner_404(sm, make_owner, enqueued, api: httpx.AsyncClient) -> None:  # type: ignore[no-untyped-def]
    a, b = await make_owner(), await make_owner()
    seeded = await _briefed(sm, a, enqueued)
    mine = await api.get(f"/v1/documents/{seeded.ref.document_id}/brief", headers=a.auth)
    assert mine.status_code == 200
    r = await api.get(f"/v1/documents/{seeded.ref.document_id}/brief", headers=b.auth)
    assert r.status_code == 404 and "Cited" not in r.text and "Medalist" not in r.text
    unknown = await api.get("/v1/documents/00000000-0000-4000-8000-000000000000/brief", headers=a.auth)
    assert unknown.status_code == 404
    assert (await api.get(f"/v1/documents/{seeded.ref.document_id}/brief")).status_code == 401


async def test_verified_filter(sm, make_owner, enqueued, api: httpx.AsyncClient) -> None:  # type: ignore[no-untyped-def]
    o = await make_owner()
    seeded = await _briefed(sm, o, enqueued)
    url = f"/v1/documents/{seeded.ref.document_id}/brief"
    allf = (await api.get(url, headers=o.auth)).json()["facts"]
    assert {f["status"] for f in allf} == {"verified", "unverified"} and len(allf) == 5
    explicit = (await api.get(url, params={"include_unverified": "true"}, headers=o.auth)).json()["facts"]
    assert explicit == allf
    only = (await api.get(url, params={"include_unverified": "false"}, headers=o.auth)).json()["facts"]
    assert [f["value"] for f in only] == ["Medalist Diversified REIT, Inc.", "2020-06-01", "$1,250,000"]
    assert {f["status"] for f in only} == {"verified"}


async def test_list_enriched_and_filter(sm, make_owner, enqueued, api: httpx.AsyncClient) -> None:  # type: ignore[no-untyped-def]
    o = await make_owner()
    long_summary = "Sentence about the agreement. " * 20
    briefed = await _briefed(
        sm,
        o,
        enqueued,
        [fact("party", "Acme Consulting LLC", handle(SERVICES, "Medalist"))],
        summary=long_summary,
    )
    other = await seed_document(sm, o.id, title="Plain")  # no brief, no category
    r = await api.get("/v1/documents", headers=o.auth)
    items = {i["id"]: i for i in r.json()["items"]}
    mine = items[str(briefed.ref.document_id)]
    assert mine["category"] == "contract" and mine["tags"] == ["consulting", "real estate"]
    assert mine["status"] == "enriched"
    snippet = mine["summary_snippet"]
    assert 0 < len(snippet) <= 200 and snippet.startswith("Sentence about the agreement.")
    assert snippet.endswith("…")
    plain = items[str(other.document_id)]
    assert (plain["category"], plain["tags"], plain["summary_snippet"]) == (None, [], None)
    by_cat = await api.get("/v1/documents", params={"category": "contract"}, headers=o.auth)
    assert [i["id"] for i in by_cat.json()["items"]] == [str(briefed.ref.document_id)]
    by_tag = await api.get("/v1/documents", params={"tag": "consulting"}, headers=o.auth)
    assert [i["id"] for i in by_tag.json()["items"]] == [str(briefed.ref.document_id)]
    none = await api.get("/v1/documents", params={"category": "policy"}, headers=o.auth)
    assert none.json()["items"] == []


async def test_deleted_404(sm, make_owner, enqueued, api: httpx.AsyncClient) -> None:  # type: ignore[no-untyped-def]
    o = await make_owner()
    seeded = await _briefed(sm, o, enqueued)
    url = f"/v1/documents/{seeded.ref.document_id}/brief"
    assert (await api.get(url, headers=o.auth)).status_code == 200
    async with sm() as s:
        await s.execute(
            text("UPDATE documents SET deleted_at = now() WHERE id = :d"), {"d": seeded.ref.document_id}
        )
        await s.commit()
    assert (await api.get(url, headers=o.auth)).status_code == 404
