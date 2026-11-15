"""No collector configured, no exporter: the default stack has no OTLP endpoint to talk to."""

import logging
from collections.abc import Callable

import pytest
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter, SimpleLogRecordProcessor
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.core.settings import Settings
from app.observability import otel


@pytest.fixture
def isolated_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    """`setup_telemetry` installs process-global providers once; keep this test from installing any."""
    monkeypatch.setattr(otel, "_provider", None)
    monkeypatch.setattr(otel, "_configured", True)  # skip the library instrumentors
    monkeypatch.setattr(otel.trace, "set_tracer_provider", lambda provider: None)
    monkeypatch.setattr(otel.metrics, "set_meter_provider", lambda provider: None)
    monkeypatch.setattr(otel, "set_logger_provider", lambda provider: None)


def _settings(endpoint: str) -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        database_url="postgresql+asyncpg://u:p@h/db",
        redis_url="redis://h/0",
        otel_exporter_otlp_endpoint=endpoint,
    )


def _fail(*_a: object, **_k: object) -> None:
    raise AssertionError("an OTLP exporter was built without an endpoint")


def test_empty_endpoint_builds_no_exporter(monkeypatch: pytest.MonkeyPatch, isolated_providers: None) -> None:
    for name in ("OTLPSpanExporter", "OTLPMetricExporter", "OTLPLogExporter"):
        monkeypatch.setattr(otel, name, _fail)
    otel.setup_telemetry(_settings(""), "vault-test")


def test_an_endpoint_builds_the_exporters(monkeypatch: pytest.MonkeyPatch, isolated_providers: None) -> None:
    built: list[str] = []

    def stub(name: str) -> Callable[..., object]:
        return lambda *a, **k: built.append(name) or object()

    for name in ("OTLPSpanExporter", "OTLPMetricExporter", "OTLPLogExporter"):
        monkeypatch.setattr(otel, name, stub(name))
    monkeypatch.setattr(
        otel, "BatchSpanProcessor", lambda exporter: SimpleSpanProcessor(InMemorySpanExporter())
    )
    monkeypatch.setattr(otel, "PeriodicExportingMetricReader", lambda exporter: InMemoryMetricReader())
    monkeypatch.setattr(
        otel,
        "BatchLogRecordProcessor",
        lambda exporter: SimpleLogRecordProcessor(InMemoryLogRecordExporter()),
    )
    monkeypatch.setattr(otel, "LoggingHandler", lambda **k: logging.NullHandler())
    otel.setup_telemetry(_settings("http://collector:4317"), "vault-test")
    assert built == ["OTLPSpanExporter", "OTLPMetricExporter", "OTLPLogExporter"]
