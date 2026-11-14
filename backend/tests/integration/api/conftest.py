import asyncio
import io
from collections.abc import AsyncIterator, Iterator

import httpx
import pytest
from fastapi import FastAPI
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import configure_logging, get_logger
from app.main import create_app


class Body(BaseModel):
    content: str
    n: int


def _test_app() -> FastAPI:
    app = create_app()
    log = get_logger("test")

    @app.get("/_test/log/{tag}")
    async def log_route(tag: str, delay: float = 0.0) -> dict[str, str]:
        await asyncio.sleep(delay)
        log.info("probe", tag=tag)
        return {"tag": tag}

    @app.get("/_test/boom")
    async def boom() -> None:
        raise RuntimeError("secret internal detail at /srv/app/x.py")

    @app.post("/_test/body")
    async def body(b: Body) -> Body:
        return b

    return app


@pytest.fixture
def log_stream() -> Iterator[io.StringIO]:
    buf = io.StringIO()
    configure_logging("INFO", stream=buf)
    yield buf
    configure_logging("INFO")


@pytest.fixture
async def client(log_stream: io.StringIO) -> AsyncIterator[httpx.AsyncClient]:
    app = _test_app()
    configure_logging("INFO", stream=log_stream)  # create_app() reconfigures to stdout
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def db_client(session: AsyncSession) -> AsyncIterator[httpx.AsyncClient]:
    """Client whose requests use the test transaction (rolled back after the test)."""
    from app.api.deps import get_db
    from app.core.auth import CurrentUser

    app = _test_app()

    @app.get("/_test/whoami")
    async def whoami(user: CurrentUser) -> dict[str, object]:
        return {"id": str(user.id), "email": user.email, "is_admin": user.is_admin}

    @app.post("/_test/whoami")
    async def whoami_post(user: CurrentUser) -> dict[str, object]:
        return {"email": user.email}

    async def _db() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_db] = _db
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
