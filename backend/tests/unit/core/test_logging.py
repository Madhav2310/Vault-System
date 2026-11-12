import io
import json

import structlog

from app.core.logging import bind, configure_logging, get_logger


def test_output_is_json() -> None:
    buf = io.StringIO()
    configure_logging("INFO", stream=buf)
    try:
        bind(run_id="r1")
        get_logger("t").info("hello", n=1)
        import logging

        logging.getLogger("uvicorn.error").warning("stdlib line")
    finally:
        structlog.contextvars.clear_contextvars()
        configure_logging("INFO")
    lines = [json.loads(line) for line in buf.getvalue().splitlines()]
    assert len(lines) == 2
    assert lines[0]["event"] == "hello" and lines[0]["run_id"] == "r1" and lines[0]["level"] == "info"
    assert lines[1]["event"] == "stdlib line" and "timestamp" in lines[1]
