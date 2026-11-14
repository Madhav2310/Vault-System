"""Maintenance tasks (beat-scheduled, `maintenance` queue). All idempotent."""

from sqlalchemy import text

from app.api.idempotency import TTL_HOURS, purge_expired
from app.chat.reaper import reap_abandoned_runs
from app.core.logging import get_logger
from app.ingestion.identifiers import backfill_tsv_ids
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


JOB_EVENTS_RETENTION_DAYS = 90


async def purge_job_events(sm: SessionFactory, days: int = JOB_EVENTS_RETENTION_DAYS) -> int:
    async with sm() as s:
        res = await s.execute(
            text("DELETE FROM job_events WHERE created_at < now() - make_interval(days => :d)"), {"d": days}
        )
        await s.commit()
    n = int(res.rowcount or 0)  # type: ignore[attr-defined]
    _log.info("job_events_purged", deleted=n)
    return n


@celery_app.task(name="vault.maintenance.purge_job_events")
def purge_job_events_task() -> int:
    return run_db(purge_job_events)


async def backfill_tsv_ids_task_fn(sm: SessionFactory) -> int:
    """One-off: fill `tsv_ids` for chunks indexed before the identifier index existed. Idempotent: a second
    run updates 0."""
    async with sm() as s:
        n = await backfill_tsv_ids(s)
    _log.info("tsv_ids_backfilled", updated=n)
    return n


@celery_app.task(name="vault.maintenance.backfill_tsv_ids")
def backfill_tsv_ids_task() -> int:
    return run_db(backfill_tsv_ids_task_fn)


@celery_app.task(name="vault.maintenance.reap_chat_runs")
def reap_chat_runs_task() -> int:
    return run_db(reap_abandoned_runs)
