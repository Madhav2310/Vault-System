"""Document purge task (`maintenance` queue). Idempotent: see app.documents.delete."""

import uuid

from app.core.settings import get_settings
from app.documents.delete import PURGE_TASK, purge_document, purge_pending
from app.storage.factory import get_object_store
from app.worker.celery_app import celery_app
from app.worker.runtime import SessionFactory, run_db


@celery_app.task(name=PURGE_TASK)
def purge_document_task(document_id: str) -> int:
    async def go(sm: SessionFactory) -> int:
        return await purge_document(sm, get_object_store(get_settings()), uuid.UUID(document_id))

    return run_db(go)


@celery_app.task(name="vault.maintenance.purge_pending")
def purge_pending_task() -> int:
    async def go(sm: SessionFactory) -> int:
        return await purge_pending(sm, get_object_store(get_settings()))

    return run_db(go)
