"""Health endpoints (design §12).

`/healthz`: process liveness, touches nothing. `/readyz`: every dependency the API needs, checked
concurrently with a per-check timeout; 503 + per-check status if any fails.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Annotated, Literal

import asyncpg
import httpx
import redis.asyncio as aioredis
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.engine import make_url

from app.core.settings import Settings, get_settings
from app.storage.factory import get_object_store

router = APIRouter()
CHECK_TIMEOUT_S = 2.0


class Check(BaseModel):
    status: Literal["ok", "fail"]
    duration_ms: float
    error: str | None = None  # exception class name only; never connection strings


class Readiness(BaseModel):
    status: Literal["ok", "fail"]
    checks: dict[str, Check]


async def _database(s: Settings) -> None:
    url = make_url(s.database_url).set(drivername="postgresql").render_as_string(hide_password=False)
    conn = await asyncpg.connect(url, timeout=CHECK_TIMEOUT_S)
    try:
        await conn.fetchval("SELECT 1")
    finally:
        await conn.close()


async def _redis(s: Settings) -> None:
    client = aioredis.from_url(s.redis_url, socket_connect_timeout=CHECK_TIMEOUT_S)
    try:
        await client.ping()
    finally:
        await client.aclose()


async def _object_store(s: Settings) -> None:
    store = get_object_store(s)
    await store.put("_health/probe", b"ok")
    if await store.get("_health/probe") != b"ok":
        raise RuntimeError("object store probe mismatch")


async def _reranker(s: Settings) -> None:
    async with httpx.AsyncClient(timeout=CHECK_TIMEOUT_S) as client:
        (await client.get(f"{s.reranker_url}/healthz")).raise_for_status()


async def _providers(s: Settings) -> None:
    missing = [n for n in ("anthropic_api_key", "openai_api_key") if getattr(s, n) is None]
    if missing:
        raise LookupError(f"missing settings: {', '.join(missing)}")


CHECKS: dict[str, Callable[[Settings], Awaitable[None]]] = {
    "database": _database,
    "redis": _redis,
    "object_store": _object_store,
    "reranker": _reranker,
    "providers": _providers,
}


async def _run(fn: Callable[[Settings], Awaitable[None]], s: Settings) -> Check:
    started = time.perf_counter()
    try:
        await asyncio.wait_for(fn(s), CHECK_TIMEOUT_S)
        error = None
    except Exception as exc:  # report, never raise: readiness must always answer
        error = str(exc) if isinstance(exc, LookupError) else type(exc).__name__
    ms = round((time.perf_counter() - started) * 1000, 1)
    return Check(status="fail" if error else "ok", duration_ms=ms, error=error)


@router.get("/healthz", include_in_schema=False)
async def healthz() -> dict[str, str]:
    """Process liveness only; never touches dependencies."""
    return {"status": "ok"}


@router.get("/readyz", include_in_schema=False, response_model=Readiness)
async def readyz(settings: Annotated[Settings, Depends(get_settings)]) -> JSONResponse:
    results = await asyncio.gather(*(_run(fn, settings) for fn in CHECKS.values()))
    checks = dict(zip(CHECKS, results, strict=True))
    ok = all(c.status == "ok" for c in checks.values())
    body = Readiness(status="ok" if ok else "fail", checks=checks)
    return JSONResponse(body.model_dump(), status_code=200 if ok else 503)
