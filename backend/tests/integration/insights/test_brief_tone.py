"""Brief tone (brief@2): stored from the same extract call, cited like facts, served by the API."""

from typing import Any

import httpx
from sqlalchemy import text

from app.insights.brief import EMPTY_PARAMS_HASH, PROMPT_VERSION
from tests.integration.insights.test_brief_api import EVIDENCE_KEYS, check_shape
from tests.support.brief_kit import brief_gateway, brief_reply, merge_reply, request_inputs
from tests.support.brief_seed import (
    DELIVERY,
    LAW,
    SERVICES,
    ChunkSpec,
    Seeded,
    handle,
    insight_rows,
    run_brief,
    seed_ready_version,
)


async def _briefed(sm, owner, enqueued, **kw: Any) -> Seeded:  # type: ignore[no-untyped-def]
    seeded = await seed_ready_version(sm, owner.id, [ChunkSpec(SERVICES)])
    gw, _, _ = brief_gateway([brief_reply([], **kw)])
    await run_brief(sm, seeded.ref.job_id, gw, enqueued)
    return seeded


async def test_tone_stored_with_code_copied_evidence(sm, make_owner, enqueued) -> None:  # type: ignore[no-untyped-def]
    o = await make_owner()
    seeded = await seed_ready_version(sm, o.id, [ChunkSpec(SERVICES)])
    reply = brief_reply(tone="formal", tone_note="Legal drafting.", tone_evidence=[handle(SERVICES, "fee")])
    gw, prov, _ = brief_gateway([reply])
    assert await run_brief(sm, seeded.ref.job_id, gw, enqueued) == "finished"
    assert len(prov.requests) == 1, "tone comes from the same extract call"
    (row,) = await insight_rows(sm, seeded.ref.version_id)
    t = row["content"]["tone"]
    assert (t["label"], t["note"], t["status"], t["reason"]) == (
        "formal",
        "Legal drafting.",
        "verified",
        None,
    )
    assert [e["cited_text"].startswith("The fee is") for e in t["evidence"]] == [True]


async def test_tone_without_resolvable_citation_is_unverified(sm, make_owner, enqueued) -> None:  # type: ignore[no-untyped-def]
    o = await make_owner()
    seeded = await seed_ready_version(sm, o.id, [ChunkSpec(SERVICES)])
    gw, _, _ = brief_gateway([brief_reply(tone="negative", tone_evidence=[])])
    await run_brief(sm, seeded.ref.job_id, gw, enqueued)
    (row,) = await insight_rows(sm, seeded.ref.version_id)
    t = row["content"]["tone"]
    assert (t["label"], t["status"], t["reason"], t["evidence"]) == (
        "negative",
        "unverified",
        "NO_CITATION",
        [],
    )


async def test_map_reduce_tone_evidence_from_parts(sm, make_owner, enqueued) -> None:  # type: ignore[no-untyped-def]
    o = await make_owner()
    chunks = [ChunkSpec(SERVICES, ("S1",), 80), ChunkSpec(DELIVERY, ("S2",), 80), ChunkSpec(LAW, ("S3",), 80)]
    seeded = await seed_ready_version(sm, o.id, chunks)
    gw, prov, _ = brief_gateway(
        [
            brief_reply(tone="formal", tone_evidence=[handle(SERVICES, "Medalist")]),
            brief_reply(tone="adversarial", tone_evidence=[handle(DELIVERY, "Payment")]),
            brief_reply(tone="formal", tone_evidence=[handle(LAW, "governed")]),
            merge_reply(tone="mixed"),
        ]
    )
    await run_brief(sm, seeded.ref.job_id, gw, enqueued, brief_max_evidence_tokens=100)
    parts = request_inputs(prov.requests[3])["parts"]
    assert [p["tone"] for p in parts] == ["formal", "adversarial", "formal"]
    (row,) = await insight_rows(sm, seeded.ref.version_id)
    t = row["content"]["tone"]
    assert (t["label"], t["note"], t["status"]) == ("mixed", "Merged tone note.", "verified")
    assert len(t["evidence"]) == 3, "citations come from the map step, up to three"


async def test_tone_in_brief_response(sm, make_owner, enqueued, api: httpx.AsyncClient) -> None:  # type: ignore[no-untyped-def]
    o = await make_owner()
    seeded = await _briefed(
        sm, o, enqueued, tone="adversarial", tone_note="Demands.", tone_evidence=[handle(SERVICES, "fee")]
    )
    body = (await api.get(f"/v1/documents/{seeded.ref.document_id}/brief", headers=o.auth)).json()
    tone = body["tone"]
    assert set(tone) == {"label", "note", "status", "evidence"}
    assert (tone["label"], tone["note"], tone["status"]) == ("adversarial", "Demands.", "verified")
    (e,) = tone["evidence"]
    check_shape(e, EVIDENCE_KEYS)
    assert e["cited_text"].startswith("The fee is")


async def test_unverified_tone_labelled_and_filtered(
    sm, make_owner, enqueued, api: httpx.AsyncClient
) -> None:  # type: ignore[no-untyped-def]
    o = await make_owner()
    seeded = await _briefed(sm, o, enqueued, tone="mixed", tone_evidence=[])  # no citation at all
    url = f"/v1/documents/{seeded.ref.document_id}/brief"
    tone = (await api.get(url, headers=o.auth)).json()["tone"]
    assert (tone["label"], tone["status"], tone["evidence"]) == ("mixed", "unverified", [])
    strict = (await api.get(url, params={"include_unverified": "false"}, headers=o.auth)).json()
    assert strict["tone"] is None


async def test_brief_without_tone_serves_null(sm, make_owner, enqueued, api: httpx.AsyncClient) -> None:  # type: ignore[no-untyped-def]
    o = await make_owner()
    seeded = await seed_ready_version(sm, o.id, [ChunkSpec(SERVICES)])
    async with sm() as s:  # a brief stored before tone existed has no `tone` key
        await s.execute(
            text(
                "INSERT INTO document_insights (id, owner_id, version_id, kind, params_hash, params, "
                "content, "
                "status, prompt_version) VALUES (gen_random_uuid(), :o, :v, 'brief', :h, '{}', "
                "CAST(:c AS jsonb), 'done', :pv)"
            ),
            {
                "o": o.id,
                "v": seeded.ref.version_id,
                "h": EMPTY_PARAMS_HASH,
                "pv": PROMPT_VERSION,
                "c": '{"document_type":"x","category":"other","tags":[],"summary":"s",'
                '"suggested_questions":[],"facts":[],"reasons":[],"partial":false}',
            },
        )
        await s.commit()
    body = (await api.get(f"/v1/documents/{seeded.ref.document_id}/brief", headers=o.auth)).json()
    assert body["status"] == "ready" and body["tone"] is None
