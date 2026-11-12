"""Celery application (design §6.6, §13.1).

Delivery is at-least-once: tasks ack late and are re-queued if a worker dies, so every task must be
idempotent. Durable job state lives in `processing_jobs`, never only in Celery.
"""

from celery import Celery
from celery.signals import worker_process_init
from kombu import Queue

from app.core.settings import get_settings

QUEUES = ("ingest", "ai", "maintenance")

# task name → queue. Every task registered in app.worker.tasks must appear here.
ROUTES: dict[str, str] = {
    "vault.ping": "maintenance",
}

VISIBILITY_TIMEOUT_S = 3600  # > longest stage time (design §6.6)


def make_celery() -> Celery:
    s = get_settings()
    app = Celery("vault", broker=s.redis_url, backend=s.redis_url, include=["app.worker.tasks.ping"])
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
    )
    return app


celery_app = make_celery()


@worker_process_init.connect
def _init_telemetry(**_: object) -> None:
    """Prefork children start without providers: install OTel in each (not fired for the solo pool)."""
    from app.observability.otel import setup_telemetry

    settings = get_settings()
    setup_telemetry(settings, settings.otel_service_name)
