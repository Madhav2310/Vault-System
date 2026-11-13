"""S12.08: the brief fields of GET /v1/metrics/documents: hand-computed values and owner scoping."""

import json
import uuid
from collections.abc import Callable
from typing import Any

import httpx
from sqlalchemy import text

from tests.support.brief_seed import ChunkSpec, seed_ready_version


def facts(*statuses: str) -> str:
    return json.dumps(
        {
            "document_type": "x",
            "category": "contract",
            "tags": [],
            "summary": "s",
            "suggested_questions": [],
            "facts": [
                {"field": "key_point", "value": f"v{i}", "status": s, "reason": None, "evidence": []}
                for i, s in enumerate(statuses)
            ],
            "reasons": [],
            "partial": False,
        }
    )


async def seed_doc(
    sm: Any, owner_id: uuid.UUID, *, status: str, category: str | None, brief: str | None, costs: list[str]
) -> uuid.UUID:
    s = await seed_ready_version(sm, owner_id, [ChunkSpec("A fact.")], status=status)
    async with sm() as db:
        await db.execute(
            text("UPDATE documents SET category = :c WHERE id = :d"), {"c": category, "d": s.ref.document_id}
        )
        if brief is not None:
            await db.execute(
                text(
                    "INSERT INTO document_insights (id, owner_id, version_id, kind, params_hash, params, "
                    "content, status, prompt_version) VALUES (gen_random_uuid(), :o, :v, 'brief', 'h', '{}', "
                    "CAST(:c AS jsonb), 'done', 1)"
                ),
                {"o": owner_id, "v": s.ref.version_id, "c": brief},
            )
        for i, cost in enumerate(costs):
            await db.execute(
                text(
                    "INSERT INTO llm_calls (id, owner_id, job_id, purpose, lane, provider, model, attempt, "
                    "cost_usd, status) VALUES (gen_random_uuid(), :o, :j, :p, 'background', 'fake', 'm', 1, "
                    ":c, 'ok')"
                ),
                {
                    "o": owner_id,
                    "j": s.ref.job_id,
                    "p": "brief_extract" if i == 0 else "brief_merge",
                    "c": cost,
                },
            )
        await db.commit()
    return s.ref.document_id


async def seed_owner_a(sm: Any, owner_id: uuid.UUID) -> None:
    await seed_doc(
        sm,
        owner_id,
        status="enriched",
        category="contract",
        brief=facts("verified", "verified", "verified", "unverified"),
        costs=["0.010000", "0.002000"],
    )
    await seed_doc(
        sm,
        owner_id,
        status="enriched",
        category="contract",
        brief=facts("verified", "verified"),
        costs=["0.020000"],
    )
    await seed_doc(sm, owner_id, status="ready", category=None, brief=None, costs=[])  # not enriched yet
    d4 = await seed_doc(
        sm, owner_id, status="enriched", category="financial_report", brief=facts(), costs=["0.004000"]
    )
    d5 = await seed_doc(  # deleted: nothing of it counts
        sm, owner_id, status="enriched", category="policy", brief=facts("unverified"), costs=["9.000000"]
    )
    async with sm() as db:
        await db.execute(text("UPDATE documents SET deleted_at = now() WHERE id = :d"), {"d": d5})
        # a summary insight and a summary call are not part of the brief numbers
        await db.execute(
            text(
                "INSERT INTO document_insights (id, owner_id, version_id, kind, params_hash, params, "
                "content, status, prompt_version) SELECT gen_random_uuid(), owner_id, current_version_id, "
                "'summary', "
                "'h2', '{}', CAST(:c AS jsonb), 'done', 1 FROM documents WHERE id = :d"
            ),
            {"c": json.dumps({"facts": [{"status": "verified"}] * 50}), "d": d4},
        )
        await db.commit()


async def test_values(api: httpx.AsyncClient, sm: Any, mk_owner: Callable[..., Any]) -> None:
    a = await mk_owner()
    await seed_owner_a(sm, a.id)
    body = (await api.get("/v1/metrics/documents", headers=a.auth)).json()
    assert body["documents"] == 4  # d5 is deleted
    assert body["enriched"] == 3 and body["by_status"]["enriched"] == 3
    assert body["by_category"] == {
        "contract": 2,
        "financial_report": 1,
    }  # the uncategorized document is absent
    # costs per document: 0.010 + 0.002 = 0.012; 0.020; 0.004 -> total 0.036, median 0.012
    assert body["brief_cost_usd"] == {"total": 0.036, "per_document_p50": 0.012}
    assert body["facts"] == {"total": 6, "verified_ratio": 0.833}  # 5 of 6


async def test_owner_scoped(api: httpx.AsyncClient, sm: Any, mk_owner: Callable[..., Any]) -> None:
    a, b = await mk_owner(), await mk_owner()
    empty = (await api.get("/v1/metrics/documents", headers=b.auth)).json()
    assert empty["enriched"] == 0 and empty["by_category"] == {}
    assert empty["brief_cost_usd"] == {"total": 0.0, "per_document_p50": None}
    assert empty["facts"] == {"total": 0, "verified_ratio": None}
    await seed_owner_a(sm, a.id)
    await seed_doc(
        sm, b.id, status="enriched", category="policy", brief=facts("unverified"), costs=["0.500000"]
    )
    rb = (await api.get("/v1/metrics/documents", headers=b.auth)).json()
    assert rb["enriched"] == 1 and rb["by_category"] == {"policy": 1}
    assert rb["brief_cost_usd"] == {"total": 0.5, "per_document_p50": 0.5}
    assert rb["facts"] == {"total": 1, "verified_ratio": 0.0}
    ra = (await api.get("/v1/metrics/documents", headers=a.auth)).json()
    assert ra["enriched"] == 3 and ra["facts"] == {"total": 6, "verified_ratio": 0.833}
    assert ra["brief_cost_usd"]["total"] == 0.036
