# ruff: noqa: S608  (SQL is built only from constant fragments; every value is a bound parameter)
"""Ingestion-pipeline metrics (design 12) from `job_events` and `processing_jobs`.

Event facts used (see app/ingestion/runner.py): a stage success appends `status='succeeded'`; a failed
attempt appends `status='error'` with `detail = {error_code, permanent}`. A job is finished when its final
stage succeeded: the job is `succeeded` and the event's stage is still the job's `current_stage`.
"""

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.jobs import DISPATCH_GRACE_S
from app.metrics import WINDOWS_S, num, scope_params, scope_sql


async def processing_metrics(db: AsyncSession, owner_id: uuid.UUID | None, window: str) -> dict[str, Any]:
    """`owner_id=None` is global scope. `window` is a key of WINDOWS_S (validated by the API)."""
    secs = WINDOWS_S[window]
    p = scope_params(owner_id, w=secs)
    events = (
        "FROM job_events e JOIN processing_jobs j ON j.id = e.job_id "
        f"WHERE e.created_at >= now() - make_interval(secs => :w) {scope_sql(owner_id, 'j.owner_id')} "
    )
    stages = (
        await db.execute(
            text(
                "SELECT e.stage, count(*) AS n, "
                "percentile_cont(0.5) WITHIN GROUP (ORDER BY e.duration_ms) AS p50, "
                "percentile_cont(0.95) WITHIN GROUP (ORDER BY e.duration_ms) AS p95 "
                f"{events} AND e.status = 'succeeded' GROUP BY e.stage ORDER BY e.stage"
            ),
            p,
        )
    ).all()
    completed = (
        await db.execute(
            text(
                f"SELECT count(DISTINCT e.job_id) {events} AND e.status = 'succeeded' "
                "AND j.status = 'succeeded' AND e.stage = j.current_stage"
            ),
            p,
        )
    ).scalar_one()
    failures = await db.execute(
        text(
            "SELECT COALESCE(e.detail ->> 'error_code', 'unknown') AS code, count(*) "
            f"{events} AND e.status = 'error' GROUP BY 1 ORDER BY 2 DESC, 1"
        ),
        p,
    )
    failures_by_code = {code: n for code, n in failures}
    # A retry is a non-permanent error that scheduled another attempt: not the event that exhausted the job.
    retries = (
        await db.execute(
            text(
                f"SELECT count(*) {events} AND e.status = 'error' "
                "AND COALESCE((e.detail ->> 'permanent')::boolean, false) = false "
                "AND NOT (j.status = 'dead' AND e.attempt = j.attempt_count AND e.stage = j.current_stage)"
            ),
            p,
        )
    ).scalar_one()
    # Point-in-time: the jobs the sweeper (app/ingestion/sweeper.py) would re-dispatch right now. Actual
    # re-dispatches are not recorded anywhere durable, so a historical count is not available.
    cand = (
        await db.execute(
            text(
                "SELECT count(*) FROM processing_jobs j WHERE true "
                f"{scope_sql(owner_id, 'j.owner_id')} AND ("
                "(j.status IN ('pending', 'dispatched') "
                " AND COALESCE(j.last_dispatched_at, j.created_at) < now() - make_interval(secs => :g) "
                " AND (j.lease_expires_at IS NULL OR j.lease_expires_at < now())) "
                "OR (j.status = 'running' AND j.lease_expires_at < now()) "
                "OR (j.status = 'failed' AND j.next_attempt_at <= now()))"
            ),
            scope_params(owner_id, g=DISPATCH_GRACE_S),
        )
    ).scalar_one()
    # Age since the job last made progress (latest event, else its creation). Not windowed.
    oldest = (
        await db.execute(
            text(
                "SELECT extract(epoch FROM now() - min(COALESCE("
                "(SELECT max(e.created_at) FROM job_events e WHERE e.job_id = j.id), j.created_at))) "
                f"FROM processing_jobs j WHERE j.status = 'pending' {scope_sql(owner_id, 'j.owner_id')}"
            ),
            scope_params(owner_id),
        )
    ).scalar_one()
    stage_runs = sum(s.n for s in stages)
    return {
        "scope": "owner" if owner_id is not None else "global",
        "window": window,
        "throughput": {
            "jobs_completed": completed,
            "stage_runs": stage_runs,
            "jobs_per_hour": round(completed / (secs / 3600), 3),
        },
        "stage_duration_ms": {s.stage: {"count": s.n, "p50": num(s.p50), "p95": num(s.p95)} for s in stages},
        "failures_by_error_code": failures_by_code,
        "retries": retries,
        "redispatch_candidates": cand,
        "oldest_pending_age_seconds": num(oldest),
    }
