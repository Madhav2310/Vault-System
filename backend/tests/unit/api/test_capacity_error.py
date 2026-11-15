import httpx
from fastapi import FastAPI

from app.api.errors import install_error_handlers
from app.llm.errors import CapacityError


async def test_a_capacity_error_reaching_a_request_is_a_retryable_503() -> None:
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/x")
    async def x() -> None:
        raise CapacityError("interactive lane at capacity")

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/x")
    body = r.json()
    assert (r.status_code, body["code"], body["retryable"]) == (503, "capacity", True)
