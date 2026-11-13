"""Celery application (design §6.6, §13.1).

Delivery is at-least-once: tasks ack late and are re-queued if a worker dies, so every task must be
idempotent. Durable job state lives in `processing_jobs`, never only in Celery.
"""

from celery import Celery
from celery.signals import worker_process_init
from kombu import Queue

from app.core.settings import get_settings
from app.llm.routing import ModelRouter

QUEUES = ("ingest", "ai", "maintenance")

# task name → queue. Every task registered in app.worker.tasks must appear here.
ROUTES: dict[str, str] = {
    "vault.ping": "maintenance",
    "vault.maintenance.purge_idempotency_keys": "maintenance",
    "vault.maintenance.purge_job_events": "maintenance",
    "vault.maintenance.sweep_jobs": "maintenance",
    "vault.maintenance.purge_pending": "maintenance",
    "vault.maintenance.backfill_tsv_ids": "maintenance",
    "vault.maintenance.reap_chat_runs": "maintenance",
    "vault.maintenance.purge_document": "maintenance",
    "vault.ingest.stage": "ingest",
    "vault.ai.stage": "ai",
    "vault.ai.summary": "ai",
}

# beat: name → (task, seconds). Every entry's task must be routed.
BEAT: dict[str, tuple[str, float]] = {
    "purge-idempotency-keys": ("vault.maintenance.purge_idempotency_keys", 3600.0),
    "purge-job-events": ("vault.maintenance.purge_job_events", 86400.0),
    "sweep-jobs": ("vault.maintenance.sweep_jobs", 60.0),
    "purge-pending": ("vault.maintenance.purge_pending", 600.0),
    "reap-chat-runs": ("vault.maintenance.reap_chat_runs", 60.0),
}

VISIBILITY_TIMEOUT_S = 3600  # > longest stage time (design §6.6)


def make_celery() -> Celery:
    s = get_settings()
    ModelRouter.from_settings(s)  # workers fail fast on a bad routing config too
    app = Celery(
        "vault",
        broker=s.redis_url,
        backend=s.redis_url,
        include=[
            "app.worker.tasks.ping",
            "app.worker.tasks.maintenance",
            "app.worker.tasks.ingest",
            "app.worker.tasks.purge",
            "app.worker.tasks.summaries",
        ],
    )
    app.conf.update(
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        broker_transport_options={"visibility_timeout": VISIBILITY_TIMEOUT_S},
        result_backend_transport_options={"visibility_timeout": VISIBILITY_TIMEOUT_S},
        task_queues=[Queue(q) for q in QUEUES],
        task_default_queue="maintenance",
        task_routes={name: {"queue": q} for name, q in ROUTES.items()},
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        result_expires=3600,
        broker_connection_retry_on_startup=True,
        worker_hijack_root_logger=False,
        beat_schedule={name: {"task": task, "schedule": every} for name, (task, every) in BEAT.items()},
    )
    return app


celery_app = make_celery()


@worker_process_init.connect
def _init_telemetry(**_: object) -> None:
    """Prefork children start without providers: install OTel in each (not fired for the solo pool)."""
    from app.observability.otel import setup_telemetry

    settings = get_settings()
    setup_telemetry(settings, settings.otel_service_name)
