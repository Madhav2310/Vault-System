"""S12.04: the brief stage over a FakeProvider gateway and the real database."""

import uuid
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy import text

from app.ingestion.embed_index import IndexStage
from app.ingestion.runner import run_job
from app.insights.stage import BriefStage
from app.llm.prompts import default_registry
from app.llm.providers import fake
from tests.support.brief_kit import brief_gateway, brief_reply, merge_reply, request_inputs, settings
from tests.support.brief_seed import (
    DELIVERY,
    LAW,
    SERVICES,
    ChunkSpec,
    handle,
    insight_rows,
    run_brief,
    seed_ready_version,
)
from tests.support.embed import add_chunks
from tests.support.jobs import job_row, make_job, version_status

EXTRACT = default_registry().get("brief_extract", 3).body
MERGE = default_registry().get("brief_merge", 2).body


def fact(field: str, value: str, *handles: str) -> dict[str, Any]:
    return {"field": field, "value": value, "evidence": list(handles)}


def services_facts() -> list[dict[str, Any]]:
    return [
        fact("party", "Medalist Diversified REIT, Inc.", handle(SERVICES, "Medalist")),
        fact("effective_date", "2020-06-01", handle(SERVICES, "Medalist")),
        fact("amount", "$1,250,000", handle(SERVICES, "fee")),
        fact("amount", "$9,999", handle(SERVICES, "fee")),  # not in the cited sentence
        fact("key_point", "No citation at all"),
    ]


async def test_single_call(sm, make_owner, enqueued) -> None:  # type: ignore[no-untyped-def]
    o = await make_owner()
    seeded = await seed_ready_version(sm, o.id, [ChunkSpec(SERVICES)])
    gw, prov, ledger = brief_gateway([brief_reply(services_facts())])
    assert await run_brief(sm, seeded.ref.job_id, gw, enqueued) == "finished"
    assert len(prov.requests) == 1 and prov.requests[0].system == EXTRACT
    assert [r.purpose for r in ledger.records] == ["brief_extract"]
    assert ledger.records[0].lane == "background" and ledger.records[0].job_id == seeded.ref.job_id
    rows = await insight_rows(sm, seeded.ref.version_id)
    assert len(rows) == 1
    row = rows[0]
    assert (row["kind"], row["status"], row["prompt_version"], row["owner_id"]) == ("brief", "done", 2, o.id)
    assert row["params_hash"] and row["params"] == {} and row["model"]
    c = row["content"]
    statuses = {f["value"]: (f["status"], f["reason"]) for f in c["facts"]}
    assert statuses == {
        "Medalist Diversified REIT, Inc.": ("verified", None),
        "2020-06-01": ("verified", None),
        "$1,250,000": ("verified", None),
        "$9,999": ("unverified", "VALUE_NOT_IN_CITATION"),
        "No citation at all": ("unverified", "NO_CITATION"),
    }
    assert c["category"] == "contract" and c["tags"] == ["consulting", "real estate"] and not c["partial"]
    assert len(c["suggested_questions"]) == 3
    async with sm() as s:  # the document row carries the brief's category and tags
        d = (
            await s.execute(
                text("SELECT category, tags FROM documents WHERE id = :d"), {"d": seeded.ref.document_id}
            )
        ).one()
    assert (d.category, d.tags) == ("contract", ["consulting", "real estate"])


async def _three_groups(sm, make_owner):  # type: ignore[no-untyped-def]
    o = await make_owner()
    chunks = [
        ChunkSpec(SERVICES, ("S1",), 80),
        ChunkSpec(DELIVERY, ("S2",), 80),
        ChunkSpec(LAW, ("S3",), 80),
    ]
    return o, await seed_ready_version(sm, o.id, chunks)


def _group_replies() -> list[fake.FakeReply]:
    return [
        brief_reply(
            [
                fact("party", "Acme Consulting LLC", handle(SERVICES, "Medalist")),
                fact("effective_date", "2020-06-01", handle(SERVICES, "Medalist")),
            ]
        ),
        brief_reply(
            [
                fact("party", "acme consulting llc.", handle(DELIVERY, "reports")),  # duplicate of group 1
                fact("deadline", "within 30 days of each invoice", handle(DELIVERY, "Payment")),
            ]
        ),
        brief_reply([fact("governing_law", "State of Delaware", handle(LAW, "governed"))]),
    ]


async def test_map_reduce_dedup_facts(sm, make_owner, enqueued) -> None:  # type: ignore[no-untyped-def]
    _, seeded = await _three_groups(sm, make_owner)
    gw, prov, ledger = brief_gateway([*_group_replies(), merge_reply()])
    out = await run_brief(sm, seeded.ref.job_id, gw, enqueued, brief_max_evidence_tokens=100)
    assert out == "finished"
    assert [r.system for r in prov.requests] == [EXTRACT, EXTRACT, EXTRACT, MERGE]
    assert [r.purpose for r in ledger.records] == ["brief_extract"] * 3 + ["brief_merge"]
    assert {r.role for r in ledger.records} == {"balanced", "fast"}
    (row,) = await insight_rows(sm, seeded.ref.version_id)
    facts = row["content"]["facts"]
    assert [(f["field"], f["value"]) for f in facts] == [
        ("party", "Acme Consulting LLC"),
        ("effective_date", "2020-06-01"),
        ("deadline", "within 30 days of each invoice"),
        ("governing_law", "State of Delaware"),
    ]
    party = facts[0]
    assert len(party["evidence"]) == 2, "citations of the duplicate are united"
    assert row["content"]["summary"] == "Merged summary of the whole agreement."
    # the merge call saw the map outputs as plain JSON, never evidence
    merge_msg = prov.requests[3].messages[0]["content"]
    assert "<evidence>" not in merge_msg
    assert len(request_inputs(prov.requests[3])["parts"]) == 3
    assert [(await version_status(sm, seeded.ref.version_id))[0]] == ["enriched"]


async def test_merge_keeps_citations(sm, make_owner, enqueued) -> None:  # type: ignore[no-untyped-def]
    _, seeded = await _three_groups(sm, make_owner)
    gw, _, _ = brief_gateway([*_group_replies(), merge_reply()])
    await run_brief(sm, seeded.ref.job_id, gw, enqueued, brief_max_evidence_tokens=100)
    (row,) = await insight_rows(sm, seeded.ref.version_id)
    async with sm() as s:
        stored = {
            r.id: r.original_text
            for r in await s.execute(
                text("SELECT id, original_text FROM chunks WHERE version_id = :v"),
                {"v": seeded.ref.version_id},
            )
        }
    assert set(stored) == set(seeded.chunk_ids)
    refs = [e for f in row["content"]["facts"] for e in f["evidence"]]
    assert len(refs) == 5
    for e in refs:
        assert uuid.UUID(e["chunk_id"]) in stored, "every chunk_id belongs to the version"
        assert e["version_id"] == str(seeded.ref.version_id)
        assert stored[uuid.UUID(e["chunk_id"])][e["char_start"] : e["char_end"]] == e["cited_text"]
        assert e["version_no"] == 1


async def test_enriched_status(sm, make_owner, enqueued, api: httpx.AsyncClient) -> None:  # type: ignore[no-untyped-def]
    o = await make_owner()
    seeded = await seed_ready_version(sm, o.id, [ChunkSpec(SERVICES)])
    r = await api.get(f"/v1/documents/{seeded.ref.document_id}", headers=o.auth)
    assert r.json()["status"] == "ready"
    gw, _, _ = brief_gateway([brief_reply(services_facts())])
    await run_brief(sm, seeded.ref.job_id, gw, enqueued)
    r = await api.get(f"/v1/documents/{seeded.ref.document_id}", headers=o.auth)
    assert r.status_code == 200 and r.json()["status"] == "enriched"
    assert r.json()["versions"][0]["status"] == "enriched"
    async with sm() as s:
        at = (
            await s.execute(
                text("SELECT enriched_at FROM document_versions WHERE id = :v"), {"v": seeded.ref.version_id}
            )
        ).scalar_one()
    assert at is not None
    assert (await job_row(sm, seeded.ref.job_id))["status"] == "succeeded"


async def test_failure_isolated(sm, make_owner, enqueued) -> None:  # type: ignore[no-untyped-def]
    o = await make_owner()
    seeded = await seed_ready_version(sm, o.id, [ChunkSpec(SERVICES)])
    gw, prov, _ = brief_gateway([fake.bad_request()])
    assert await run_brief(sm, seeded.ref.job_id, gw, enqueued) == "finished"
    assert len(prov.requests) == 1
    assert (await version_status(sm, seeded.ref.version_id)) == ("ready", None)
    assert (await job_row(sm, seeded.ref.job_id))["status"] == "succeeded"
    (row,) = await insight_rows(sm, seeded.ref.version_id)
    assert row["status"] == "failed" and row["content"] == {"error_code": "bad_request"}
    # a schema-invalid reply is isolated the same way
    seeded2 = await seed_ready_version(sm, o.id, [ChunkSpec(SERVICES)])
    gw2, _, _ = brief_gateway([fake.text("not json")] * 3)
    assert await run_brief(sm, seeded2.ref.job_id, gw2, enqueued) == "finished"
    assert (await version_status(sm, seeded2.ref.version_id))[0] == "ready"
    assert (await insight_rows(sm, seeded2.ref.version_id))[0]["status"] == "failed"


async def test_failed_row_is_retried_by_a_rerun(sm, make_owner, enqueued) -> None:  # type: ignore[no-untyped-def]
    o = await make_owner()
    seeded = await seed_ready_version(sm, o.id, [ChunkSpec(SERVICES)])
    gw, _, _ = brief_gateway([fake.bad_request()])
    await run_brief(sm, seeded.ref.job_id, gw, enqueued)
    await _rewind(sm, seeded.ref.job_id)
    gw2, prov2, _ = brief_gateway([brief_reply(services_facts())])
    await run_brief(sm, seeded.ref.job_id, gw2, enqueued)
    assert len(prov2.requests) == 1
    rows = await insight_rows(sm, seeded.ref.version_id)
    assert [r["status"] for r in rows] == ["done"]
    assert (await version_status(sm, seeded.ref.version_id))[0] == "enriched"


async def _rewind(sm, job_id) -> None:  # type: ignore[no-untyped-def]
    async with sm() as s:
        await s.execute(
            text("UPDATE processing_jobs SET status = 'pending', current_stage = 'brief' WHERE id = :j"),
            {"j": job_id},
        )
        await s.commit()


async def test_cost_cap(sm, make_owner, enqueued) -> None:  # type: ignore[no-untyped-def]
    _, seeded = await _three_groups(sm, make_owner)
    gw, prov, _ = brief_gateway([*_group_replies(), merge_reply()])
    out = await run_brief(
        sm, seeded.ref.job_id, gw, enqueued, brief_max_evidence_tokens=100, brief_max_usd=1e-12
    )
    assert out == "finished"
    assert len(prov.requests) == 1, "the first map call ran; the cap stopped the rest (no merge of one part)"
    (row,) = await insight_rows(sm, seeded.ref.version_id)
    assert row["status"] == "done" and row["content"]["partial"] is True
    assert "BRIEF_PARTIAL_BUDGET" in row["content"]["reasons"]
    assert [f["value"] for f in row["content"]["facts"]] == ["Acme Consulting LLC", "2020-06-01"]


async def test_cost_cap_merges_what_exists(sm, make_owner, enqueued) -> None:  # type: ignore[no-untyped-def]
    o, probe = await _three_groups(sm, make_owner)
    gw0, _, ledger0 = brief_gateway([brief_reply()])
    await run_brief(sm, probe.ref.job_id, gw0, enqueued)  # one 3-chunk call: price of one fake call
    one_call = ledger0.records[0].cost_usd
    assert one_call > Decimal(0)
    seeded = await seed_ready_version(
        sm,
        o.id,
        [ChunkSpec(SERVICES, ("S1",), 80), ChunkSpec(DELIVERY, ("S2",), 80), ChunkSpec(LAW, ("S3",), 80)],
    )
    gw, prov, _ = brief_gateway([*_group_replies(), merge_reply()])
    cap = float(one_call * Decimal("1.5"))  # room for a second map call, not a third
    await run_brief(sm, seeded.ref.job_id, gw, enqueued, brief_max_evidence_tokens=100, brief_max_usd=cap)
    assert [r.system for r in prov.requests] == [EXTRACT, EXTRACT, MERGE]
    (row,) = await insight_rows(sm, seeded.ref.version_id)
    assert row["content"]["partial"] is True and "BRIEF_PARTIAL_BUDGET" in row["content"]["reasons"]
    assert len(request_inputs(prov.requests[2])["parts"]) == 2


async def test_idempotent_rerun(sm, make_owner, enqueued) -> None:  # type: ignore[no-untyped-def]
    o = await make_owner()
    seeded = await seed_ready_version(sm, o.id, [ChunkSpec(SERVICES)])
    gw, prov, _ = brief_gateway([brief_reply(services_facts())])
    await run_brief(sm, seeded.ref.job_id, gw, enqueued)
    (before,) = await insight_rows(sm, seeded.ref.version_id)
    await _rewind(sm, seeded.ref.job_id)
    gw2, prov2, ledger2 = brief_gateway([brief_reply(services_facts())])
    assert await run_brief(sm, seeded.ref.job_id, gw2, enqueued) == "finished"
    assert prov2.requests == [] and ledger2.records == []
    (after,) = await insight_rows(sm, seeded.ref.version_id)
    assert after["id"] == before["id"] and after["content"] == before["content"]
    assert len(prov.requests) == 1
    assert (await version_status(sm, seeded.ref.version_id))[0] == "enriched"


async def test_deleted_skipped(sm, make_owner, enqueued) -> None:  # type: ignore[no-untyped-def]
    o = await make_owner()
    seeded = await seed_ready_version(sm, o.id, [ChunkSpec(SERVICES)])
    async with sm() as s:
        await s.execute(
            text("UPDATE documents SET deleted_at = now() WHERE id = :d"), {"d": seeded.ref.document_id}
        )
        await s.commit()
    gw, prov, ledger = brief_gateway([brief_reply(services_facts())])
    assert await run_brief(sm, seeded.ref.job_id, gw, enqueued) == "deleted"
    assert prov.requests == [] and ledger.records == []
    assert await insight_rows(sm, seeded.ref.version_id) == []
    assert (await version_status(sm, seeded.ref.version_id))[0] == "ready"


async def test_no_gateway_leaves_version_ready(sm, make_owner, enqueued) -> None:  # type: ignore[no-untyped-def]
    o = await make_owner()
    seeded = await seed_ready_version(sm, o.id, [ChunkSpec(SERVICES)])
    assert await run_brief(sm, seeded.ref.job_id, None, enqueued) == "finished"
    assert await insight_rows(sm, seeded.ref.version_id) == []
    assert (await version_status(sm, seeded.ref.version_id))[0] == "ready"


async def test_superseded_version_keeps_status_and_document_fields(  # type: ignore[no-untyped-def]
    sm, make_owner, enqueued
) -> None:
    o = await make_owner()
    seeded = await seed_ready_version(sm, o.id, [ChunkSpec(SERVICES)])
    async with sm() as s:  # a newer version took over the document while the stage ran
        await s.execute(
            text("UPDATE document_versions SET status = 'superseded' WHERE id = :v"),
            {"v": seeded.ref.version_id},
        )
        await s.execute(
            text("UPDATE documents SET current_version_id = NULL, category = 'other' WHERE id = :d"),
            {"d": seeded.ref.document_id},
        )
        await s.commit()
    gw, _, _ = brief_gateway([brief_reply(services_facts())])
    assert await run_brief(sm, seeded.ref.job_id, gw, enqueued) == "finished"
    assert (await version_status(sm, seeded.ref.version_id))[0] == "superseded"
    async with sm() as s:
        q = text("SELECT category FROM documents WHERE id = :d")
        cat = (await s.execute(q, {"d": seeded.ref.document_id})).scalar_one()
    assert cat == "other"


async def test_pipeline_continues_from_index_to_brief(  # type: ignore[no-untyped-def]
    sm, make_owner, enqueued, store
) -> None:
    """`index` marks the version ready and the job moves on to `brief` instead of finishing."""
    o = await make_owner()
    ref = await make_job(sm, o.id, stage="index")
    await add_chunks(sm, o.id, ref, 2, embedded=True)
    gw, prov, _ = brief_gateway([brief_reply()])
    services = {"store": store, "settings": settings(), "gateway": gw}
    stages: dict[str, Any] = {"index": IndexStage(), "brief": BriefStage()}
    order = ("index", "brief")
    first = await run_job(sm, ref.job_id, "w", stages, enqueued, order=order, services=services)
    assert first == "advanced"
    assert (await version_status(sm, ref.version_id))[0] == "ready"
    assert (await job_row(sm, ref.job_id))["current_stage"] == "brief"
    assert enqueued.calls[-1] == ("brief", ref.job_id)
    second = await run_job(sm, ref.job_id, "w", stages, enqueued, order=order, services=services)
    assert second == "finished" and len(prov.requests) == 1
    assert (await version_status(sm, ref.version_id))[0] == "enriched"
