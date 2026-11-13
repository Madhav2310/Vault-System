"""Maintenance tasks (beat-scheduled, `maintenance` queue). All idempotent."""

from app.api.idempotency import TTL_HOURS, purge_expired
from app.core.logging import get_logger
from app.worker.celery_app import celery_app
from app.worker.runtime import SessionFactory, run_db

_log = get_logger("vault.maintenance")


async def purge_idempotency_keys(sm: SessionFactory, ttl_hours: int = TTL_HOURS) -> int:
    async with sm() as s:
        n = await purge_expired(s, ttl_hours)
    _log.info("idempotency_keys_purged", deleted=n)
    return n


@celery_app.task(name="vault.maintenance.purge_idempotency_keys")
def purge_idempotency_keys_task() -> int:
    return run_db(purge_idempotency_keys)
