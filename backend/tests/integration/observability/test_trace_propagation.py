from collections.abc import Iterator

import httpx
import pytest
from celery.contrib.testing.worker import start_worker
from fastapi import FastAPI
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.core.settings import get_settings
from app.observability.otel import instrument_fastapi, setup_telemetry
from app.worker.celery_app import celery_app
from app.worker.tasks.ping import ping


@pytest.fixture
def memory_broker() -> Iterator[None]:
    """In-memory Celery broker for one test, then restore (e2e tests share the process)."""
    keys = ("broker_url", "result_backend", "broker_transport_options")
    saved = {k: celery_app.conf[k] for k in keys}
    celery_app.conf.update(
        broker_url="memory://", result_backend="cache+memory://", broker_transport_options={}
    )
    yield
    celery_app.conf.update(saved)
    celery_app.close()  # drop pooled connections to the memory broker
    celery_app.__dict__.pop("amqp", None)  # cached with the memory broker's routing/producer state
    if hasattr(celery_app._local, "backend"):  # result backend is cached per thread
        del celery_app._local.backend


async def test_api_to_celery_same_trace(memory_broker: None) -> None:
    exporter = InMemorySpanExporter()
    provider = setup_telemetry(get_settings(), "vault-test", span_processor=SimpleSpanProcessor(exporter))
    celery_app.conf.update(
        broker_url="memory://", result_backend="cache+memory://", broker_transport_options={}
    )

    app = FastAPI()

    @app.post("/enqueue")
    async def enqueue() -> dict[str, str]:
        result = ping.apply_async(args=["via-api"], queue="maintenance")
        return {"task_id": result.id}

    instrument_fastapi(app, tracer_provider=provider)

    with start_worker(
        celery_app, pool="solo", perform_ping_check=False, queues=["maintenance"], loglevel="WARNING"
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
            resp = await client.post("/enqueue")
        assert resp.status_code == 200
        assert ping.AsyncResult(resp.json()["task_id"]).get(timeout=15) == "via-api"

    provider.force_flush()
    spans = exporter.get_finished_spans()
    names = {s.name for s in spans}
    server = [s for s in spans if s.name.startswith("POST")]
    run = [s for s in spans if s.name == "run/vault.ping"]
    assert server, f"no FastAPI server span in {names}"
    assert run, f"no Celery worker span in {names}"
    assert {s.context.trace_id for s in server + run} == {server[0].context.trace_id}
