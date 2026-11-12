"""Request context: request ID in/out, contextvars bound for the request's lifetime, one access log line."""

import re
import time
import uuid

from starlette.types import ASGIApp, Message, Receive, Scope, Send
from structlog.contextvars import bound_contextvars

from app.core.logging import get_logger

_VALID_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_log = get_logger("vault.access")


class RequestContextMiddleware:
    """Pure ASGI (no BaseHTTPMiddleware) so streaming responses aren't buffered."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = dict(scope["headers"]).get(b"x-request-id", b"").decode("latin-1")
        request_id = incoming if _VALID_ID.match(incoming) else uuid.uuid4().hex
        status = 500
        started = time.perf_counter()

        async def send_with_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message.setdefault("headers", [])
                message["headers"].append((b"x-request-id", request_id.encode()))
            await send(message)

        with bound_contextvars(request_id=request_id):
            try:
                await self.app(scope, receive, send_with_id)
            finally:
                route = scope.get("route")
                _log.info(
                    "request",
                    method=scope["method"],
                    route=getattr(route, "path", None),  # template, never the raw path (may carry IDs)
                    status=status,
                    duration_ms=round((time.perf_counter() - started) * 1000, 1),
                )
