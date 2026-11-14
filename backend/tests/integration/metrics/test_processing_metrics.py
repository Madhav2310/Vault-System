"""GET /v1/metrics/processing: values from a seeded job_events fixture, window validation, isolation."""

import json
import uuid
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from tests.support.jobs import make_job


async def event(
    sm: Any,
    job_id: uuid.UUID,
    stage: str,
    status: str,
    *,
    minutes_ago: float,
    ms: int | None = None,
    attempt: int = 1,
    detail: dict[str, Any] | None = None,
) -> None:
    async with sm() as s:
        await s.execute(
            text(
                "INSERT INTO job_events (id, job_id, stage, status, attempt, duration_ms, detail, "
                "created_at) "
                "VALUES (gen_random_uuid(), :j, :st, :s, :a, :ms, CAST(:d AS jsonb), "
                "now() - make_interval(secs => CAST(:m AS float8) * 60))"
            ),
            {
                "j": job_id,
                "st": stage,
                "s": status,
                "a": attempt,
                "ms": ms,
                "d": json.dumps(detail) if detail is not None else None,
                "m": minutes_ago,
            },
        )
        await s.commit()


async def patch_job(sm: Any, job_id: uuid.UUID, **cols: str) -> None:
    """`cols` values are SQL expressions (constants from this file), e.g. attempt_count='5'."""
    sets = ", ".join(f"{k} = {v}" for k, v in cols.items())
    async with sm() as s:
        await s.execute(text(f"UPDATE processing_jobs SET {sets} WHERE id = :j"), {"j": job_id})
        await s.commit()


def ago(minutes: int) -> str:
    return f"now() - interval '{minutes} minutes'"


async def seed_owner_a(sm: Any, owner_id: uuid.UUID) -> None:
    """Minutes are relative to now. See `test_values` for the arithmetic built on this fixture."""
    err429 = {"error_code": "provider_429", "permanent": False}
    # J1, J2, J3: finished jobs (status succeeded, current_stage = the final stage 'brief').
    for extract_ms, extract_ago, brief_ms, brief_ago in (
        (1000, 30, 400, 20),
        (3000, 90, 600, 80),
        (2000, 10, 800, 5),
    ):
        j = await make_job(sm, owner_id, stage="brief", status="succeeded")
        await event(sm, j.job_id, "extract", "succeeded", minutes_ago=extract_ago, ms=extract_ms)
        await event(sm, j.job_id, "brief", "succeeded", minutes_ago=brief_ago, ms=brief_ms)
    # J4: failed once at embed, retry due now (a re-dispatch candidate).
    j4 = await make_job(sm, owner_id, stage="embed", status="failed")
    await patch_job(sm, j4.job_id, attempt_count="1", next_attempt_at=ago(1))
    await event(sm, j4.job_id, "extract", "succeeded", minutes_ago=40, ms=1500)
    await event(sm, j4.job_id, "embed", "error", minutes_ago=35, ms=50, detail=err429)
    # J5: dead after 5 attempts: attempt 4 was a retried error, attempt 5 the exhausting one.
    j5 = await make_job(sm, owner_id, stage="embed", status="dead")
    await patch_job(sm, j5.job_id, attempt_count="5", error_code="'provider_timeout'")
    await event(sm, j5.job_id, "embed", "error", minutes_ago=180, ms=50, attempt=4, detail=err429)
    to = {"error_code": "provider_timeout", "permanent": False}
    await event(sm, j5.job_id, "embed", "error", minutes_ago=170, ms=50, attempt=5, detail=to)
    # J6: permanent failure at extract (not a retry).
    j6 = await make_job(sm, owner_id, stage="extract", status="dead")
    await patch_job(sm, j6.job_id, attempt_count="1", error_code="'corrupt_file'")
    perm = {"error_code": "corrupt_file", "permanent": True}
    await event(sm, j6.job_id, "extract", "error", minutes_ago=3, ms=20, detail=perm)
    # J7: pending at chunk; last event 120 min ago (age 7200 s); created 180 min ago.
    j7 = await make_job(sm, owner_id, stage="chunk", status="pending")
    await patch_job(sm, j7.job_id, created_at=ago(180))
    await event(sm, j7.job_id, "extract", "succeeded", minutes_ago=120, ms=100)
    # J8: pending, no events, created 30 min ago (age 1800 s). J9: pending, created just now.
    j8 = await make_job(sm, owner_id, stage="extract", status="pending")
    await patch_job(sm, j8.job_id, created_at=ago(30))
    await make_job(sm, owner_id, stage="extract", status="pending")
    # J10: running with a live lease (not a candidate). J11: running with an expired lease (candidate).
    j10 = await make_job(sm, owner_id, stage="extract", status="running")
    await patch_job(sm, j10.job_id, lease_expires_at="now() + interval '5 minutes'")
    j11 = await make_job(sm, owner_id, stage="extract", status="running")
    await patch_job(sm, j11.job_id, lease_expires_at=ago(1))


async def test_values(api: httpx.AsyncClient, sm: Any, mk_owner: Callable[..., Any]) -> None:
    a = await mk_owner()
    b = await mk_owner()
    await seed_owner_a(sm, a.id)
    jb = await make_job(sm, b.id, stage="brief", status="succeeded")  # owner B noise, never in A's numbers
    await event(sm, jb.job_id, "brief", "succeeded", minutes_ago=1, ms=99999)
    await event(
        sm, jb.job_id, "embed", "error", minutes_ago=1, detail={"error_code": "b_only", "permanent": True}
    )

    # --- window 24h ---
    r = await api.get("/v1/metrics/processing", headers=a.auth)  # default window is 24h
    assert r.status_code == 200
    m = r.json()
    assert m["window"] == "24h" and m["scope"] == "owner"
    # Finished jobs (final-stage success event in window): J1 (-20m), J2 (-80m), J3 (-5m) = 3; /24 h = 0.125.
    # Stage runs (succeeded events): extract J1 J2 J3 J4 J7 = 5, brief J1 J2 J3 = 3 -> 8.
    assert m["throughput"] == {"jobs_completed": 3, "stage_runs": 8, "jobs_per_hour": 0.125}
    # extract ms [100, 1000, 1500, 2000, 3000]: p50 = 1500; p95 rank 0.95 * 4 = 3.8 -> 2000 + 0.8 * 1000.
    # brief ms [400, 600, 800]: p50 = 600; p95 rank 1.9 -> 600 + 0.9 * 200 = 780.
    assert m["stage_duration_ms"] == {
        "extract": {"count": 5, "p50": 1500.0, "p95": 2800.0},
        "brief": {"count": 3, "p50": 600.0, "p95": 780.0},
    }
    # Error events: J4 429, J5 429 (attempt 4), J5 timeout (attempt 5), J6 corrupt_file.
    assert m["failures_by_error_code"] == {"provider_429": 2, "provider_timeout": 1, "corrupt_file": 1}
    # Retries = non-permanent errors that scheduled another attempt: J4, J5 attempt 4 = 2.
    # (J5 attempt 5 exhausted the job, J6 was permanent.)
    assert m["retries"] == 2
    # Candidates the sweeper would pick up now: J4 (retry due), J7 + J8 (pending past grace, no lease),
    # J11 (expired lease) = 4. J9 is inside the grace period, J10 has a live lease.
    assert m["redispatch_candidates"] == 4
    # Oldest pending job: J7, last activity 120 min ago = 7200 s (J8 1800 s, J9 ~0 s).
    assert 7200 <= m["oldest_pending_age_seconds"] < 7260

    # --- window 1h: J2 and J7 events, and J5's, fall outside ---
    m1 = (await api.get("/v1/metrics/processing?window=1h", headers=a.auth)).json()
    assert m1["window"] == "1h"
    # Finished in 1h: J1, J3 = 2 -> 2.0 per hour. Stage runs: extract J1 J3 J4 = 3, brief J1 J3 = 2.
    assert m1["throughput"] == {"jobs_completed": 2, "stage_runs": 5, "jobs_per_hour": 2.0}
    # extract ms [1000, 1500, 2000]: p50 1500; p95 1500 + 0.9 * 500 = 1950.
    # brief [400, 800]: p50 600; p95 400 + 0.95 * 400 = 780.
    assert m1["stage_duration_ms"] == {
        "extract": {"count": 3, "p50": 1500.0, "p95": 1950.0},
        "brief": {"count": 2, "p50": 600.0, "p95": 780.0},
    }
    assert m1["failures_by_error_code"] == {"provider_429": 1, "corrupt_file": 1}  # J4, J6
    assert m1["retries"] == 1  # J4
    assert m1["redispatch_candidates"] == 4 and 7200 <= m1["oldest_pending_age_seconds"] < 7260

    # --- owner B sees only their own ---
    mb = (await api.get("/v1/metrics/processing", headers=b.auth)).json()
    assert mb["throughput"] == {"jobs_completed": 1, "stage_runs": 1, "jobs_per_hour": round(1 / 24, 3)}
    assert mb["stage_duration_ms"] == {"brief": {"count": 1, "p50": 99999.0, "p95": 99999.0}}
    assert mb["failures_by_error_code"] == {"b_only": 1}
    assert (
        mb["retries"] == 0 and mb["redispatch_candidates"] == 0 and mb["oldest_pending_age_seconds"] is None
    )


async def test_empty_state(api: httpx.AsyncClient, mk_owner: Callable[..., Any]) -> None:
    a = await mk_owner()
    r = await api.get("/v1/metrics/processing?window=7d", headers=a.auth)
    assert r.status_code == 200
    assert r.json() == {
        "scope": "owner",
        "window": "7d",
        "throughput": {"jobs_completed": 0, "stage_runs": 0, "jobs_per_hour": 0.0},
        "stage_duration_ms": {},
        "failures_by_error_code": {},
        "retries": 0,
        "redispatch_candidates": 0,
        "oldest_pending_age_seconds": None,
    }


@pytest.mark.parametrize("bad", ["2h", "", "24", "30d", "all"])
async def test_window_validation(api: httpx.AsyncClient, mk_owner: Callable[..., Any], bad: str) -> None:
    a = await mk_owner()
    r = await api.get(f"/v1/metrics/processing?window={bad}", headers=a.auth)
    assert r.status_code == 422
    assert r.json()["code"] == "validation_error"


async def test_global_scope(api: httpx.AsyncClient, sm: Any, mk_owner: Callable[..., Any]) -> None:
    a = await mk_owner()
    b = await mk_owner(admin=True)
    jb = await make_job(sm, a.id, stage="brief", status="succeeded")
    await event(sm, jb.job_id, "brief", "succeeded", minutes_ago=1, ms=5)
    assert (await api.get("/v1/metrics/processing?scope=global", headers=a.auth)).status_code == 403
    g = await api.get("/v1/metrics/processing?scope=global", headers=b.auth)
    assert g.status_code == 200 and g.json()["scope"] == "global"
    assert g.json()["throughput"]["jobs_completed"] >= 1
    assert (await api.get("/v1/metrics/processing", headers=b.auth)).json()["throughput"][
        "jobs_completed"
    ] == 0
