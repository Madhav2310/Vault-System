# ruff: noqa: S608  (SQL is built only from constant fragments; every value is a bound parameter)
"""Chat and LLM-ledger metrics (design 12) from `rag_runs`, `chat_messages` and `llm_calls`.

A turn is a chat run: not a matrix-cell run and not a replay. Outcomes and coverage come from assistant
`chat_messages` (the run row stores neither). LLM figures cover every ledger row of the scope in the window,
including non-chat purposes such as embeddings, so totals equal the sum of `llm_calls.cost_usd`.
`llm_calls` records `lane`, not the routing role, so token groups are keyed by lane x model x purpose.
"""

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.feedback.service import feedback_summary
from app.metrics import WINDOWS_S, num, scope_params, scope_sql


def _pct(expr: str, alias: str) -> str:
    return (
        f"percentile_cont(0.5) WITHIN GROUP (ORDER BY {expr}) AS {alias}50, "
        f"percentile_cont(0.95) WITHIN GROUP (ORDER BY {expr}) AS {alias}95"
    )


async def chat_metrics(db: AsyncSession, owner_id: uuid.UUID | None, window: str) -> dict[str, Any]:
    """`owner_id=None` is global scope. `window` is a key of WINDOWS_S (validated by the API)."""
    p = scope_params(owner_id, w=WINDOWS_S[window])
    since = "created_at >= now() - make_interval(secs => :w)"
    runs = (
        f"FROM rag_runs WHERE {since} AND matrix_cell_id IS NULL AND replay_of_run_id IS NULL "
        f"{scope_sql(owner_id, 'owner_id')}"
    )
    msgs = f"FROM chat_messages WHERE {since} AND role = 'assistant' {scope_sql(owner_id, 'owner_id')}"
    calls = f"FROM llm_calls WHERE {since} {scope_sql(owner_id, 'owner_id')}"

    r = (
        (
            await db.execute(
                text(
                    "SELECT count(*) AS turns, "
                    + _pct("latency_ms", "p")
                    + ", "
                    + _pct("ttft_ms", "t")
                    + ", "
                    + _pct("cost_usd::float8", "c")
                    + f" {runs}"
                ),
                p,
            )
        )
        .mappings()
        .one()
    )

    async def grouped(sql: str) -> dict[str, int]:
        return {k: n for k, n in await db.execute(text(sql), p)}

    outcomes = await grouped(
        f"SELECT COALESCE(response_type, 'unknown'), count(*) {msgs} GROUP BY 1 ORDER BY 2 DESC, 1"
    )
    coverage = await grouped(
        f"SELECT COALESCE(coverage_state, 'none'), count(*) {msgs} GROUP BY 1 ORDER BY 2 DESC, 1"
    )
    degraded = await grouped(
        f"SELECT d, count(*) FROM (SELECT unnest(degraded) AS d {runs}) x GROUP BY 1 ORDER BY 2 DESC, 1"
    )
    tot = (
        (
            await db.execute(
                text(
                    "SELECT count(*) AS calls, COALESCE(sum(cost_usd), 0) AS cost, "
                    "COALESCE(sum(input_tokens), 0) AS inp, "
                    f"COALESCE(sum(cache_read_tokens), 0) AS cache {calls}"
                ),
                p,
            )
        )
        .mappings()
        .one()
    )
    groups = (
        (
            await db.execute(
                text(
                    "SELECT lane, model, purpose, count(*) AS calls, sum(input_tokens) AS inp, "
                    "sum(output_tokens) AS outp, sum(cache_read_tokens) AS cache, sum(cost_usd) AS cost "
                    f"{calls} GROUP BY lane, model, purpose ORDER BY lane, model, purpose"
                ),
                p,
            )
        )
        .mappings()
        .all()
    )
    by_purpose: dict[str, dict[str, Any]] = {}
    for g in groups:
        e = by_purpose.setdefault(g["purpose"], {"calls": 0, "cost": 0})
        e["calls"] += g["calls"]
        e["cost"] += g["cost"]
    return {
        "scope": "owner" if owner_id is not None else "global",
        "window": window,
        "turns": r["turns"],
        "outcomes": outcomes,
        "coverage": coverage,
        "latency_ms": {"p50": num(r["p50"]), "p95": num(r["p95"])},
        "ttft_ms": {"p50": num(r["t50"]), "p95": num(r["t95"])},
        "cost_per_turn_usd": {"p50": num(r["c50"], 6), "p95": num(r["c95"], 6)},
        "degraded": degraded,
        "feedback": await feedback_summary(db, owner_id, WINDOWS_S[window]),
        "llm": {
            "calls": tot["calls"],
            "total_cost_usd": num(tot["cost"], 6),
            "cache_hit_ratio": num(tot["cache"] / tot["inp"], 4) if tot["inp"] else None,
            "tokens": [
                {
                    "lane": g["lane"],
                    "model": g["model"],
                    "purpose": g["purpose"],
                    "calls": g["calls"],
                    "input_tokens": int(g["inp"]),
                    "output_tokens": int(g["outp"]),
                    "cache_read_tokens": int(g["cache"]),
                    "cost_usd": num(g["cost"], 6),
                }
                for g in groups
            ],
            "cost_by_purpose": {
                k: {"calls": v["calls"], "cost_usd": num(v["cost"], 6)}
                for k, v in sorted(by_purpose.items(), key=lambda kv: (-kv[1]["cost"], kv[0]))
            },
        },
    }
