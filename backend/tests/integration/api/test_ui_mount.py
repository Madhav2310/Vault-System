"""The built SPA is served at /app with fallback to index.html; absent a build, /app is a 404 (S19.01)."""

import pathlib
from collections.abc import AsyncIterator

import httpx
import pytest

from app.main import create_app


@pytest.fixture
def dist(tmp_path: pathlib.Path) -> pathlib.Path:
    d = tmp_path / "dist"
    (d / "assets").mkdir(parents=True)
    (d / "index.html").write_text("<!doctype html><title>Vault</title><div id=root></div>")
    (d / "assets" / "app.js").write_text("console.log(1)")
    (tmp_path / "secret.txt").write_text("outside")
    return d


async def _client(dist: pathlib.Path) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=create_app(ui_dist=dist))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_app_served_with_fallback(dist: pathlib.Path) -> None:
    async for c in _client(dist):
        for path in ("/app", "/app/", "/app/some/route"):
            r = await c.get(path, follow_redirects=True)
            assert r.status_code == 200, path
            assert "<div id=root>" in r.text
        asset = await c.get("/app/assets/app.js")
        assert asset.status_code == 200 and asset.text == "console.log(1)"
        escape = await c.get("/app/..%2fsecret.txt")
        assert "outside" not in escape.text
        # the API is unaffected
        assert (await c.get("/healthz")).status_code == 200


async def test_absent_dist_ok(tmp_path: pathlib.Path) -> None:
    async for c in _client(tmp_path / "missing"):
        assert (await c.get("/app")).status_code == 404
        assert (await c.get("/app/some/route")).status_code == 404
        assert (await c.get("/healthz")).status_code == 200
