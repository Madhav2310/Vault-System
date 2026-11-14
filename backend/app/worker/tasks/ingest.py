"""Ingestion stage tasks. The payload is the job id only; everything else is read from the DB."""

import os
import socket
import uuid

from app.core.settings import get_settings
from app.ingestion.jobs import celery_enqueue
from app.ingestion.runner import run_job
from app.ingestion.stages import default_stages
from app.ingestion.sweeper import sweep
from app.llm.gateway import build_gateway
from app.storage.factory import get_object_store
from app.worker.celery_app import celery_app
from app.worker.runtime import SessionFactory, run_db


def worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def _run_stage(job_id: str) -> str:
    async def go(sm: SessionFactory) -> str:
        settings = get_settings()
        services = {
            "store": get_object_store(settings),
            "settings": settings,
            "gateway": build_gateway(sm, settings),
        }
        return await run_job(
            sm, uuid.UUID(job_id), worker_id(), default_stages(), celery_enqueue(), services=services
        )

    return run_db(go)


@celery_app.task(name="vault.ingest.stage")
def ingest_stage(job_id: str) -> str:
    return _run_stage(job_id)


@celery_app.task(name="vault.ai.stage")
def ai_stage(job_id: str) -> str:
    return _run_stage(job_id)


@celery_app.task(name="vault.maintenance.sweep_jobs")
def sweep_jobs() -> int:
    async def go(sm: SessionFactory) -> int:
        return len(await sweep(sm, celery_enqueue()))

    return run_db(go)
