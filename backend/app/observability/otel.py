"""OpenTelemetry setup: traces, metrics and logs over OTLP to the collector.

Content is never recorded: LLM calls get a gateway span with IDs and counters only (app/llm), and the
collector redacts gen_ai message attributes as a backstop. Call `setup_telemetry()` once per process (API
startup, Celery `worker_process_init` for prefork children).
"""

import logging
from typing import Any

from fastapi import FastAPI
from opentelemetry import metrics, trace
from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.celery import CeleryInstrumentor
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from app.core.settings import Settings

EXCLUDED_URLS = "healthz,readyz,metrics"
_configured = False
_provider: TracerProvider | None = None


def resource_for(settings: Settings, service_name: str) -> Resource:
    return Resource.create(
        {
            "service.name": service_name,
            "service.version": settings.service_version,
            "deployment.environment": settings.environment,
        }
    )


def setup_telemetry(
    settings: Settings, service_name: str, *, span_processor: SpanProcessor | None = None
) -> TracerProvider:
    """Install global providers once per process. `span_processor` replaces the OTLP exporter (tests)."""
    global _configured, _provider
    if _provider is not None and span_processor is None:
        return _provider  # already configured (e.g. the solo pool fires worker_process_init in-process)
    resource = resource_for(settings, service_name)
    tracer_provider = TracerProvider(resource=resource)
    if span_processor is not None:
        tracer_provider.add_span_processor(span_processor)
    elif endpoint := settings.otel_exporter_otlp_endpoint.strip():
        tracer_provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, insecure=True))
        )
        reader = PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=endpoint, insecure=True))
        metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[reader]))
        logger_provider = LoggerProvider(resource=resource)
        logger_provider.add_log_record_processor(
            BatchLogRecordProcessor(OTLPLogExporter(endpoint=endpoint, insecure=True))
        )
        set_logger_provider(logger_provider)
        logging.getLogger().addHandler(LoggingHandler(logger_provider=logger_provider))
    # No endpoint (the default stack has no collector): spans stay in process, nothing is exported, and the
    # logs go to stdout only. `make up-full` starts the collector and points the services at it.
    trace.set_tracer_provider(tracer_provider)
    _provider = tracer_provider
    if not _configured:
        HTTPXClientInstrumentor().instrument()
        RedisInstrumentor().instrument()
        CeleryInstrumentor().instrument()  # type: ignore[no-untyped-call]
        _configured = True
    return tracer_provider


def instrument_fastapi(app: FastAPI, tracer_provider: Any = None) -> None:
    FastAPIInstrumentor.instrument_app(app, excluded_urls=EXCLUDED_URLS, tracer_provider=tracer_provider)
