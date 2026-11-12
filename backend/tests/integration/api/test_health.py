"""Readiness against the compose services (published on localhost via the Makefile's HOST_ENV)."""

import pathlib
import socket
from collections.abc import AsyncIterator, Callable

import httpx
import pytest
from pydantic import SecretStr

from app.core.settings import get_settings
from app.main import create_app


def _closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
def make_client(tmp_path: pathlib.Path) -> Callable[..., AsyncIterator[httpx.AsyncClient]]:
    async def _make(**overrides: object) -> AsyncIterator[httpx.AsyncClient]:
        base = get_settings()
        settings = base.model_copy(
            update={
                "object_store_root": str(tmp_path / "blobs"),
                "anthropic_api_key": SecretStr("test-key"),
                "openai_api_key": SecretStr("test-key"),
                **overrides,
            }
        )
        app = create_app()
        app.dependency_overrides[get_settings] = lambda: settings
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            yield c

    return _make


async def test_healthz_no_deps(make_client: Callable[..., AsyncIterator[httpx.AsyncClient]]) -> None:
    dead = f"127.0.0.1:{_closed_port()}"
    async for c in make_client(
        database_url=f"postgresql+asyncpg://x:y@{dead}/z", redis_url=f"redis://{dead}/0"
    ):
        r = await c.get("/healthz")
        assert r.status_code == 200 and r.json() == {"status": "ok"}


async def test_readyz_ok(make_client: Callable[..., AsyncIterator[httpx.AsyncClient]]) -> None:
    async for c in make_client():
        r = await c.get("/readyz")
        assert r.status_code == 200, r.json()
        body = r.json()
        assert body["status"] == "ok"
        assert set(body["checks"]) == {"database", "redis", "object_store", "reranker", "providers"}


async def test_readyz_redis_down(make_client: Callable[..., AsyncIterator[httpx.AsyncClient]]) -> None:
    async for c in make_client(redis_url=f"redis://127.0.0.1:{_closed_port()}/0"):
        r = await c.get("/readyz")
        assert r.status_code == 503
        failed = {k for k, v in r.json()["checks"].items() if v["status"] == "fail"}
        assert failed == {"redis"}


async def test_readyz_db_down(make_client: Callable[..., AsyncIterator[httpx.AsyncClient]]) -> None:
    url = f"postgresql+asyncpg://vault:secretpw@127.0.0.1:{_closed_port()}/vault"
    async for c in make_client(database_url=url):
        r = await c.get("/readyz")
        assert r.status_code == 503
        failed = {k for k, v in r.json()["checks"].items() if v["status"] == "fail"}
        assert failed == {"database"}
        assert "secretpw" not in r.text
