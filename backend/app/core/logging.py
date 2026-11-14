"""Structured JSON logging.

One JSON object per line on stdout. Correlation IDs come from contextvars (bound per request / task)
and from the active OpenTelemetry span. Never log document text, prompts or completions: IDs and
digests only.
"""

import logging
import sys
from typing import IO, Any

import structlog
from structlog.contextvars import bind_contextvars, clear_contextvars, merge_contextvars
from structlog.typing import EventDict, WrappedLogger

CONTEXT_KEYS = (
    "request_id",
    "trace_id",
    "span_id",
    "run_id",
    "job_id",
    "version_id",
    "user_id",
    "route",
    "node",
)


def _add_otel_ids(_: WrappedLogger, __: str, event: EventDict) -> EventDict:
    """Attach trace_id/span_id of the current OTel span when one is recording."""
    try:
        from opentelemetry import trace
    except ImportError:  # OpenTelemetry is optional
        return event
    ctx = trace.get_current_span().get_span_context()
    if ctx.is_valid:
        event.setdefault("trace_id", format(ctx.trace_id, "032x"))
        event.setdefault("span_id", format(ctx.span_id, "016x"))
    return event


_SHARED: list[Any] = [
    merge_contextvars,
    structlog.stdlib.add_log_level,
    structlog.stdlib.add_logger_name,
    structlog.processors.TimeStamper(fmt="iso", utc=True),
    _add_otel_ids,
]


def configure_logging(level: str = "INFO", stream: IO[str] | None = None) -> None:
    """Route structlog and stdlib (uvicorn, celery, sqlalchemy) records to one JSON stream."""
    structlog.configure(
        processors=[*_SHARED, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=_SHARED,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.format_exc_info,
                structlog.processors.JSONRenderer(),
            ],
        )
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers[:] = []
        lg.propagate = True
    logging.getLogger("uvicorn.access").disabled = True  # replaced by our request log line


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.stdlib.get_logger(name)


def bind(**values: str | int | None) -> None:
    unknown = set(values) - set(CONTEXT_KEYS)
    if unknown:
        raise ValueError(f"unknown log context keys: {sorted(unknown)}")
    bind_contextvars(**{k: v for k, v in values.items() if v is not None})


__all__ = ["CONTEXT_KEYS", "bind", "clear_contextvars", "configure_logging", "get_logger"]
