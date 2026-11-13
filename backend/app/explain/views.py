"""User view of one run (design §10.2, S18.03): status, route, sources, steps, usage and the explanation.

Every line of `explanation` is rendered by templates from the run's reason codes and facts (`templates.py`);
no model is asked to explain anything. Nothing internal leaves this module: no prompt ids, model names, trace
ids or config. All queries filter on `owner_id`; a missing or foreign run is `RunNotFound`.
"""

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat import history
from app.explain import templates as T


class RunNotFound(Exception):
    pass


async def _sources(db: AsyncSession, owner_id: uuid.UUID, run_id: uuid.UUID) -> list[dict[str, Any]]:
    """Documents (and versions) the answer cited, with the number of distinct passages and their pages."""
    rows = (
        await db.execute(
            text(
                "SELECT d.id AS document_id, d.title, v.version_no, c.id AS chunk_id, c.page_start, "
                "c.page_end "
                "FROM (SELECT DISTINCT e.chunk_id FROM segment_evidence e "
                "JOIN answer_segments s ON s.id = e.segment_id WHERE s.run_id = :r) cc "
                "JOIN chunks c ON c.owner_id = :o AND c.id = cc.chunk_id "
                "JOIN document_versions v ON v.id = c.version_id AND v.owner_id = :o "
                "JOIN documents d ON d.id = v.document_id AND d.owner_id = :o "
                "ORDER BY d.title, v.version_no, c.page_start, c.id"
            ),
            {"r": run_id, "o": owner_id},
        )
    ).mappings()
    grouped: dict[tuple[uuid.UUID, int], dict[str, Any]] = {}
    for r in rows:
        g = grouped.setdefault(
            (r["document_id"], r["version_no"]),
            {"document_title": r["title"], "version_no": r["version_no"], "pages": None, "chunks_used": 0},
        )
        g["chunks_used"] += 1
        if r["page_start"]:  # 0 = flow document, no pages
            lo, hi = r["page_start"], r["page_end"] or r["page_start"]
            g["pages"] = [lo, hi] if g["pages"] is None else [min(g["pages"][0], lo), max(g["pages"][1], hi)]
    return list(grouped.values())


async def run_view(db: AsyncSession, owner_id: uuid.UUID, run_id: uuid.UUID) -> dict[str, Any]:
    run = (
        (
            await db.execute(
                text(
                    "SELECT id, status, mode, route, reason_codes, degraded, llm_calls_used, input_tokens, "
                    "output_tokens, cost_usd, assistant_message_id FROM rag_runs "
                    "WHERE id = :r AND owner_id = :o"
                ),
                {"r": run_id, "o": owner_id},
            )
        )
        .mappings()
        .first()
    )
    if run is None:
        raise RunNotFound(str(run_id))
    steps = [
        {"node": r["node"], "status": r["status"], "duration_ms": r["duration_ms"]}
        for r in (
            await db.execute(
                text("SELECT node, status, duration_ms FROM run_steps WHERE run_id = :r ORDER BY seq"),
                {"r": run_id},
            )
        ).mappings()
    ]
    sources = await _sources(db, owner_id, run_id)
    message = None
    unsupported = 0
    if run["assistant_message_id"] is not None:
        message = await history.get_message(db, owner_id, run["assistant_message_id"])
        if message is not None and message["response_type"] == "answer":
            unsupported = sum(1 for s in message["segments"] if s["support"] != "cited")
    full_context = run["mode"] == "full_context" and "FULL_CONTEXT_UNAVAILABLE" not in run["reason_codes"]
    docs, tokens = (0, 0)
    if full_context:
        row = (
            await db.execute(
                text(
                    "SELECT count(*), COALESCE(sum(v.token_count), 0) FROM run_versions rv "
                    "JOIN document_versions v ON v.id = rv.version_id AND v.owner_id = :o "
                    "WHERE rv.run_id = :r"
                ),
                {"r": run_id, "o": owner_id},
            )
        ).one()
        docs, tokens = int(row[0]), int(row[1])
    await db.rollback()  # end the read transaction
    status = run["status"]
    explanation = _explain(
        status=status,
        response_type=message["response_type"] if message else None,
        reason_codes=list(run["reason_codes"]),
        degraded=list(run["degraded"]),
        sources=sources,
        full_context=(docs, tokens) if full_context else None,
        unsupported=unsupported,
    )
    return {
        "run_id": str(run_id),
        "status": status,
        "mode": run["mode"],
        "route": run["route"],
        "response_type": message["response_type"] if message else None,
        "reason_codes": list(run["reason_codes"]),
        "explanation": explanation,
        "sources": sources,
        "steps": steps,
        "usage": {
            "calls": run["llm_calls_used"],
            "input_tokens": run["input_tokens"],
            "output_tokens": run["output_tokens"],
            "cost_usd": float(run["cost_usd"]),
        },
        "message": message,
    }


def _explain(
    *,
    status: str,
    response_type: str | None,
    reason_codes: list[str],
    degraded: list[str],
    sources: list[dict[str, Any]],
    full_context: tuple[int, int] | None,
    unsupported: int,
) -> list[str]:
    """Deterministic lines: the headline from the run facts, then one per reason code and degradation."""
    lines: list[str] = []
    if status == "running":
        return ["The answer is still being prepared."]
    if response_type == "answer" and status != "failed":
        head = T.full_context(*full_context) if full_context else T.answered(sources)
        if head:
            lines.append(head)
        if line := T.unsupported(unsupported):
            lines.append(line)
    lines += [T.REASON_TEMPLATES.get(c, "") for c in reason_codes]
    lines += [T.DEGRADED_TEMPLATES.get(d, "") for d in degraded]
    out = list(dict.fromkeys(line for line in lines if line))
    return out or [T.NO_ANSWER]
