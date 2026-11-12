from app.worker.celery_app import QUEUES, ROUTES, celery_app


def test_reliability_settings() -> None:
    c = celery_app.conf
    assert c.task_acks_late is True
    assert c.task_reject_on_worker_lost is True
    assert c.worker_prefetch_multiplier == 1
    assert c.broker_transport_options["visibility_timeout"] == 3600
    assert {q.name for q in c.task_queues} == {"ingest", "ai", "maintenance"}


def test_routes() -> None:
    celery_app.loader.import_default_modules()
    registered = {n for n in celery_app.tasks if n.startswith("vault.")}
    assert registered == set(ROUTES), "every vault task must have a route (and vice versa)"
    for name, queue in ROUTES.items():
        assert queue in QUEUES
        route = celery_app.amqp.router.route({}, name)
        assert route["queue"].name == queue, name
