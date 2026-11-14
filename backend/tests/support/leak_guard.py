"""Content-leak guard: proves document text/prompts never reach logs or spans.

Usage in a test:  def test_x(leak_guard): ...run flow...; leak_guard.assert_no_leak(["chunk text", ...])
Captures (a) every stdlib/structlog log record emitted during the test, (b) every span finished on the
guard's tracer provider and on the global SDK provider if one is installed.
"""

import io
import json
import logging
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.core.logging import configure_logging


class LeakError(AssertionError):
    pass


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(f"{record.getMessage()} {json.dumps(record.__dict__, default=str)}")


@dataclass
class LeakGuard:
    logs: _Capture = field(default_factory=_Capture)
    exporter: InMemorySpanExporter = field(default_factory=InMemorySpanExporter)
    provider: TracerProvider = field(default_factory=TracerProvider)

    def __post_init__(self) -> None:
        self.provider.add_span_processor(SimpleSpanProcessor(self.exporter))
        glob = trace.get_tracer_provider()
        if isinstance(glob, TracerProvider):
            glob.add_span_processor(SimpleSpanProcessor(self.exporter))

    @property
    def tracer(self) -> trace.Tracer:
        return self.provider.get_tracer("leak-guard")

    def captured_text(self) -> str:
        spans = [
            json.dumps(
                {"name": sp.name, "attrs": dict(sp.attributes or {}), "events": [e.name for e in sp.events]},
                default=str,
            )
            for sp in self.exporter.get_finished_spans()
        ]
        return "\n".join([*self.logs.lines, *spans])

    def assert_no_leak(self, secrets: Iterable[str]) -> None:
        text = self.captured_text()
        found = [s for s in secrets if s and s in text]
        if found:
            raise LeakError(f"{len(found)} secret string(s) found in logs/spans (first 12 chars): "
                            f"{[s[:12] for s in found]}")  # fmt: skip


@pytest.fixture
def leak_guard() -> Iterator[LeakGuard]:
    configure_logging("DEBUG", stream=io.StringIO())  # route structlog through stdlib, silence stdout
    guard = LeakGuard()
    root = logging.getLogger()
    root.addHandler(guard.logs)
    old_level = root.level
    root.setLevel(logging.DEBUG)
    try:
        yield guard
    finally:
        root.removeHandler(guard.logs)
        root.setLevel(old_level)
        configure_logging("INFO")
