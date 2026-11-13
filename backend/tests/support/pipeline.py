"""Drive an uploaded version through the real stages (no Celery).

Upload via the API, then call run_job until the job stops advancing."""

import uuid
from typing import Any

import httpx

from app.core.settings import Settings, get_settings
from app.ingestion.runner import Outcome, Stage, run_job
from app.ingestion.stages import default_stages
from tests.support.uploads import multipart


async def upload(api: httpx.AsyncClient, owner: Any, name: str, data: bytes) -> dict[str, Any]:
    body, h = multipart({}, (name, data))
    r = await api.post("/v1/documents", content=body, headers={**h, **owner.auth})
    assert r.status_code == 202, r.text
    out: dict[str, Any] = r.json()
    return out


async def run_pipeline(
    sm: Any,
    job_id: uuid.UUID,
    store: Any,
    enqueue: Any,
    *,
    settings: Settings | None = None,
    stages: dict[str, Stage] | None = None,
    max_steps: int = 10,
) -> list[Outcome]:
    """Run stages until the job finishes, dies or a stage is unavailable (retry). Returns the outcomes."""
    services = {"store": store, "settings": settings or get_settings()}
    outcomes: list[Outcome] = []
    for _ in range(max_steps):
        out = await run_job(sm, job_id, "test-worker", stages or default_stages(), enqueue, services=services)
        outcomes.append(out)
        if out != "advanced":
            break
    return outcomes
