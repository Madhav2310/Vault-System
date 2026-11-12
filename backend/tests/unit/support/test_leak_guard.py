import pytest

from app.core.logging import get_logger
from tests.support.leak_guard import LeakError, LeakGuard, leak_guard  # noqa: F401  (fixture import)

SECRET = "ACME indemnification clause 7.3 is confidential"


def test_detects_log_leak(leak_guard: LeakGuard) -> None:  # noqa: F811
    get_logger("t").info("chunk_loaded", text=SECRET)
    with pytest.raises(LeakError):
        leak_guard.assert_no_leak([SECRET])


def test_detects_span_leak(leak_guard: LeakGuard) -> None:  # noqa: F811
    with leak_guard.tracer.start_as_current_span("embed") as span:
        span.set_attribute("input", SECRET)
    with pytest.raises(LeakError):
        leak_guard.assert_no_leak([SECRET])


def test_clean_passes(leak_guard: LeakGuard) -> None:  # noqa: F811
    get_logger("t").info("chunk_loaded", chunk_id="c1", digest="ab12", tokens=412)
    with leak_guard.tracer.start_as_current_span("embed") as span:
        span.set_attribute("chunk_count", 3)
    leak_guard.assert_no_leak([SECRET])
