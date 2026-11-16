# ruff: noqa: S608  (SQL is built only from constant fragments; every value is a bound parameter)
"""Document statistics. Each document is represented by its latest version (highest
`version_no`); soft-deleted documents are excluded."""

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.metrics import num, scope_params, scope_sql

STATUSES = ("uploaded", "processing", "ready", "enriched", "needs_ocr", "unsupported", "failed", "superseded")


def _latest(owner_id: uuid.UUID | None) -> str:
    return (
        "WITH latest AS (SELECT DISTINCT ON (v.document_id) v.id, v.document_id, v.status, v.mime, "
        "v.size_bytes, v.page_count, v.created_at, v.ready_at "
        "FROM document_versions v JOIN documents d ON d.id = v.document_id "
        f"WHERE d.deleted_at IS NULL {scope_sql(owner_id, 'd.owner_id')} "
        f"{scope_sql(owner_id, 'v.owner_id')} "
        "ORDER BY v.document_id, v.version_no DESC) "
    )


async def document_metrics(db: AsyncSession, owner_id: uuid.UUID | None) -> dict[str, Any]:
    """`owner_id=None` is global scope (admin only; the API enforces that)."""
    cte, p = _latest(owner_id), scope_params(owner_id)
    ttr = "extract(epoch FROM ready_at - created_at)"
    totals = (
        (
            await db.execute(
                text(
                    cte + "SELECT count(*) AS documents, COALESCE(sum(page_count), 0) AS pages, "
                    "COALESCE(sum(size_bytes), 0) AS storage_bytes, "
                    f"percentile_cont(0.5) WITHIN GROUP (ORDER BY {ttr}) AS p50, "
                    f"percentile_cont(0.95) WITHIN GROUP (ORDER BY {ttr}) AS p95 FROM latest"
                ),
                p,
            )
        )
        .mappings()
        .one()
    )
    by_status = dict.fromkeys(STATUSES, 0)
    for status, n in await db.execute(text(cte + "SELECT status, count(*) FROM latest GROUP BY status"), p):
        by_status[status] = n
    by_mime = {
        mime: n
        for mime, n in await db.execute(text(cte + "SELECT mime, count(*) FROM latest GROUP BY mime"), p)
    }
    chunks = (
        await db.execute(
            text(
                cte + "SELECT count(*) FROM chunks c JOIN latest l ON l.id = c.version_id "
                f"WHERE true {scope_sql(owner_id, 'c.owner_id')}"
            ),
            p,
        )
    ).scalar_one()
    by_category = {
        cat: n
        for cat, n in await db.execute(
            text(
                cte + "SELECT d.category, count(*) FROM latest l JOIN documents d ON d.id = l.document_id "
                "WHERE d.category IS NOT NULL GROUP BY d.category"
            ),
            p,
        )
    }
    facts = (
        (
            await db.execute(
                text(
                    cte + "SELECT count(*) AS total, "
                    "count(*) FILTER (WHERE f ->> 'status' = 'verified') AS verified "
                    "FROM (SELECT DISTINCT ON (i.version_id) i.content FROM document_insights i "
                    "JOIN latest l ON l.id = i.version_id "
                    f"WHERE i.kind = 'brief' AND i.status = 'done' {scope_sql(owner_id, 'i.owner_id')} "
                    "ORDER BY i.version_id, i.prompt_version DESC) b, "
                    "LATERAL jsonb_array_elements(COALESCE(b.content -> 'facts', '[]'::jsonb)) f"
                ),
                p,
            )
        )
        .mappings()
        .one()
    )
    cost = (
        (
            await db.execute(
                text(
                    "SELECT COALESCE(sum(cost), 0) AS total, "
                    "percentile_cont(0.5) WITHIN GROUP (ORDER BY cost) AS p50 FROM ("
                    "SELECT sum(c.cost_usd) AS cost FROM llm_calls c "
                    "JOIN processing_jobs j ON j.id = c.job_id "
                    "JOIN document_versions v ON v.id = j.version_id "
                    "JOIN documents d ON d.id = v.document_id "
                    "WHERE c.purpose IN ('brief_extract', 'brief_merge') AND d.deleted_at IS NULL "
                    f"{scope_sql(owner_id, 'c.owner_id')} {scope_sql(owner_id, 'd.owner_id')} "
                    "GROUP BY d.id) per_doc"
                ),
                p,
            )
        )
        .mappings()
        .one()
    )
    return {
        "scope": "owner" if owner_id is not None else "global",
        "documents": totals["documents"],
        "by_status": by_status,
        "by_mime": by_mime,
        "needs_ocr": by_status["needs_ocr"],
        "pages": int(totals["pages"]),
        "chunks": chunks,
        "storage_bytes": int(totals["storage_bytes"]),
        "time_to_ready_seconds": {"p50": num(totals["p50"]), "p95": num(totals["p95"])},
        "enriched": by_status["enriched"],
        "by_category": by_category,
        "brief_cost_usd": {"total": num(cost["total"], 6), "per_document_p50": num(cost["p50"], 6)},
        "facts": {
            "total": facts["total"],
            "verified_ratio": num(facts["verified"] / facts["total"]) if facts["total"] else None,
        },
    }
