"""Read a turn's SSE stream (`POST .../messages` with `Accept: text/event-stream`) and time it.

Client-side TTFT is the time from request start to the first `segment` frame; latency is the time to `done`.
Frames are `event: <type>` + `data: <one-line JSON>`; comment lines (heartbeats) are ignored. Pure: lines and
the clock are passed in, so it is testable without a server.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class StreamResult:
    message_id: str | None = None
    response_type: str | None = None
    ttft_ms: float | None = None  # None when no segment was streamed (an abstention)
    latency_ms: float | None = None
    events: list[str] = field(default_factory=list)  # event types in arrival order
    error: str | None = None  # error code of an `error` frame


async def read_turn_stream(
    lines: AsyncIterator[str], started: float, clock: Callable[[], float] = time.monotonic
) -> StreamResult:
    """Consume SSE lines until `done` or `error`. `started` is the clock reading at request start."""
    out = StreamResult()
    etype: str | None = None
    data: list[str] = []
    async for line in lines:
        if line == "":
            if etype is not None:
                if _dispatch(out, etype, "".join(data), (clock() - started) * 1000):
                    break
            etype, data = None, []
        elif line.startswith(":"):
            continue
        elif line.startswith("event:"):
            etype = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())
    return out


def _dispatch(out: StreamResult, etype: str, raw: str, elapsed_ms: float) -> bool:
    """Apply one frame; True when the stream is over."""
    out.events.append(etype)
    body: dict[str, Any] = json.loads(raw) if raw else {}
    if etype == "segment" and out.ttft_ms is None:
        out.ttft_ms = elapsed_ms
    elif etype == "done":
        out.message_id = body.get("message_id")
        out.response_type = body.get("response_type")
        out.latency_ms = elapsed_ms
        return True
    elif etype == "error":
        out.error = str(body.get("code", "error"))
        out.latency_ms = elapsed_ms
        return True
    return False
