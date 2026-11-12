from app.worker.celery_app import celery_app


@celery_app.task(name="vault.ping")
def ping(payload: str = "pong") -> str:
    """Liveness round-trip; idempotent and side-effect free."""
    return payload
