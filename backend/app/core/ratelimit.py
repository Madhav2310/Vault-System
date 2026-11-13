"""Per-user request rate limit (design §13.4): a Redis token bucket, one Lua call per request.

- One bucket per (user, bucket name): capacity = the bucket's limit, refilled continuously at limit/window.
- The Lua script reads Redis `TIME`, refills, takes one token and writes back in one atomic step, so parallel
  requests can never exceed the capacity and API replicas need no clock agreement.
- Redis unavailable -> fail open (design §15): the request proceeds, a warning is logged and
  `vault.ratelimit.fail_open` is counted. A limiter outage must never become an API outage.
- `enforce_rate_limit` is one router-level dependency; the bucket comes from the matched route
  (`ROUTE_BUCKETS`), everything else is `default`. `RateLimitHeadersMiddleware` adds the headers to every
  response of a limited request, including 429s and responses a handler builds itself.
"""

import asyncio
import math
import weakref
from dataclasses import dataclass
from typing import Annotated, Literal

import redis.asyncio as aioredis
from fastapi import Depends, Request
from opentelemetry import metrics
from redis.exceptions import RedisError
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.errors import DomainError
from app.core.auth import CurrentUser
from app.core.error_codes import ErrorCode
from app.core.logging import get_logger
from app.core.settings import Settings, get_settings

Bucket = Literal["chat_post", "upload", "default"]
# (method, route template) -> bucket. Anything not listed is `default`.
ROUTE_BUCKETS: dict[tuple[str, str], Bucket] = {
    ("POST", "/v1/chat/sessions/{session_id}/messages"): "chat_post",
    ("POST", "/v1/documents"): "upload",
    ("POST", "/v1/documents/{document_id}/versions"): "upload",
}
KEY_PREFIX = "vault:rl"
SCOPE_KEY = "vault.ratelimit"
REDIS_TIMEOUT_S = 0.25  # the limiter must not add real latency when Redis is slow

_log = get_logger("vault.ratelimit")
_fail_open = metrics.get_meter("vault.ratelimit").create_counter(
    "vault.ratelimit.fail_open",
    description="Requests let through because the rate limiter could not reach Redis",
)

# KEYS[1] bucket key; ARGV[1] capacity, ARGV[2] window seconds. Returns {allowed, tokens, retry_s, reset_s}
# with the floats as strings (Lua numbers returned to Redis are truncated to integers).
_LUA = """
local cap = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local rate = cap / window
local t = redis.call('TIME')
local now = tonumber(t[1]) + tonumber(t[2]) / 1000000
local h = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(h[1])
local ts = tonumber(h[2])
if tokens == nil or ts == nil then
  tokens = cap
  ts = now
end
tokens = math.min(cap, tokens + math.max(0, now - ts) * rate)
local allowed = 0
if tokens >= 1 then
  tokens = tokens - 1
  allowed = 1
end
redis.call('HSET', KEYS[1], 'tokens', tostring(tokens), 'ts', tostring(now))
redis.call('EXPIRE', KEYS[1], math.ceil(window) + 1)
local retry = 0
if allowed == 0 then retry = (1 - tokens) / rate end
return {allowed, tostring(tokens), tostring(retry), tostring((cap - tokens) / rate)}
"""


@dataclass(frozen=True)
class Decision:
    allowed: bool
    limit: int
    remaining: int
    retry_after_s: int  # seconds until one token is available (0 when allowed)
    reset_s: int  # seconds until the bucket is full again


def limit_for(bucket: Bucket, s: Settings) -> int:
    return {
        "chat_post": s.rate_limit_chat_post_per_min,
        "upload": s.rate_limit_upload_per_min,
        "default": s.rate_limit_default_per_min,
    }[bucket]


def bucket_for(method: str, template: str | None) -> Bucket:
    return ROUTE_BUCKETS.get((method.upper(), template or ""), "default")


async def consume(
    client: aioredis.Redis, user_id: object, bucket: Bucket, limit: int, window_s: int
) -> Decision:
    """Take one token. Raises RedisError / OSError / TimeoutError when Redis cannot be reached."""
    raw = await client.eval(_LUA, 1, f"{KEY_PREFIX}:{bucket}:{user_id}", limit, window_s)
    allowed, tokens, retry, reset = int(raw[0]), float(raw[1]), float(raw[2]), float(raw[3])
    return Decision(
        allowed=bool(allowed),
        limit=limit,
        remaining=max(0, math.floor(tokens)),
        retry_after_s=max(1, math.ceil(retry)) if not allowed else 0,
        reset_s=max(1, math.ceil(reset)) if reset > 0 else 0,
    )


_clients: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, aioredis.Redis]" = weakref.WeakKeyDictionary()


async def get_ratelimit_redis() -> aioredis.Redis:
    """FastAPI dependency: a short-timeout client, one per event loop (a client is bound to its loop)."""
    loop = asyncio.get_running_loop()
    client = _clients.get(loop)
    if client is None:
        client = aioredis.from_url(
            get_settings().redis_url, socket_connect_timeout=REDIS_TIMEOUT_S, socket_timeout=REDIS_TIMEOUT_S
        )
        _clients[loop] = client
    return client


def _headers(d: Decision) -> list[tuple[bytes, bytes]]:
    pairs = {"limit": d.limit, "remaining": d.remaining, "reset": d.reset_s}
    out = []
    for name, v in pairs.items():  # X-RateLimit-* (the plan) and RateLimit-* (design §12)
        out.append((f"x-ratelimit-{name}".encode(), str(v).encode()))
        out.append((f"ratelimit-{name}".encode(), str(v).encode()))
    return out


async def enforce_rate_limit(
    request: Request, user: CurrentUser, client: Annotated[aioredis.Redis, Depends(get_ratelimit_redis)]
) -> None:
    """Router-level dependency on every authenticated router (`app.main`); 429 when the bucket is empty."""
    route = request.scope.get("route")
    bucket = bucket_for(request.method, getattr(route, "path", None))
    s = get_settings()
    limit = limit_for(bucket, s)
    try:
        d = await consume(client, user.id, bucket, limit, s.rate_limit_window_s)
    except (RedisError, OSError, TimeoutError) as e:
        _fail_open.add(1, {"bucket": bucket})
        _log.warning("ratelimit_fail_open", bucket=bucket, error_type=type(e).__name__)
        return
    request.scope[SCOPE_KEY] = d
    if not d.allowed:
        raise DomainError(
            ErrorCode.RATE_LIMITED,
            f"Too many requests; retry in {d.retry_after_s} s",
            headers={"Retry-After": str(d.retry_after_s)},
        )


class RateLimitHeadersMiddleware:
    """Pure ASGI: adds the headers recorded by `enforce_rate_limit` to the response start."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            d: Decision | None = scope.get(SCOPE_KEY)
            if message["type"] == "http.response.start" and d is not None:
                message.setdefault("headers", [])
                message["headers"].extend(_headers(d))
            await send(message)

        await self.app(scope, receive, send_with_headers)
