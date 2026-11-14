"""S22.11: session cookie (httpOnly, signed, CSRF-guarded) and the /app security headers."""

import pathlib

import httpx
import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User
from app.db.seed import seed_users
from app.main import create_app

CSRF = {"X-Requested-With": "vault-ui"}
WHOAMI = "/_test/whoami"


@pytest.fixture
async def key(session: AsyncSession) -> str:
    return {r.email: r.api_key for r in await seed_users(session)}["alice@vault.local"]


async def _login(c: httpx.AsyncClient, key: str, host: str = "test") -> httpx.Response:
    return await c.post("/v1/session", json={"api_key": key}, headers={"host": host})


def _cookie(r: httpx.Response) -> str:
    return r.headers["set-cookie"].split(";", 1)[0]  # name=value


async def test_csp_and_cookie(tmp_path: pathlib.Path, db_client: httpx.AsyncClient, key: str) -> None:
    d = tmp_path / "dist"
    (d / "assets").mkdir(parents=True)
    (d / "index.html").write_text("<!doctype html><div id=root></div>")
    (d / "assets" / "a.js").write_text("1")
    transport = httpx.ASGITransport(app=create_app(ui_dist=d))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        for path in ("/app", "/app/x/y", "/app/assets/a.js"):
            r = await c.get(path, follow_redirects=True)
            csp = r.headers["content-security-policy"]
            assert "script-src 'self'" in csp and "default-src 'self'" in csp, path
            assert "'unsafe-inline'" not in csp and "'unsafe-eval'" not in csp, path
            assert "frame-ancestors 'none'" in csp and "object-src 'none'" in csp
            assert r.headers["x-content-type-options"] == "nosniff"
            assert r.headers["referrer-policy"] == "no-referrer"
        assert "content-security-policy" not in (await c.get("/healthz")).headers

    r = await _login(db_client, key)
    assert r.status_code == 204
    sc = r.headers["set-cookie"]
    assert "HttpOnly" in sc and "samesite=strict" in sc.lower() and "Path=/" in sc
    assert "Secure" in sc  # host "test" is not localhost
    assert key not in sc  # the cookie is never the raw key
    local = await _login(db_client, key, host="localhost")
    assert "Secure" not in local.headers["set-cookie"]


async def test_cookie_auth_get(db_client: httpx.AsyncClient, key: str) -> None:
    cookie = _cookie(await _login(db_client, key))
    ok = await db_client.get(WHOAMI, headers={"cookie": cookie})
    assert ok.status_code == 200 and ok.json()["email"] == "alice@vault.local"
    bad = await db_client.get(WHOAMI, headers={"cookie": cookie[:-3] + "xxx"})
    assert bad.status_code == 401
    assert (await db_client.get(WHOAMI, headers={"cookie": "vault_session=garbage"})).status_code == 401


async def test_cookie_post_requires_csrf_header(db_client: httpx.AsyncClient, key: str) -> None:
    cookie = _cookie(await _login(db_client, key))
    denied = await db_client.post(WHOAMI, headers={"cookie": cookie})
    assert denied.status_code == 403 and denied.json()["code"] == "forbidden"
    ok = await db_client.post(WHOAMI, headers={"cookie": cookie, **CSRF})
    assert ok.status_code == 200


async def test_header_auth_unchanged(db_client: httpx.AsyncClient, key: str) -> None:
    r = await db_client.post(WHOAMI, headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 200  # no CSRF header needed for bearer auth
    cookie = _cookie(await _login(db_client, key))  # a bad bearer is not rescued by a valid cookie
    bad = await db_client.get(WHOAMI, headers={"Authorization": "Bearer vk_nope", "cookie": cookie})
    assert bad.status_code == 401


async def test_bad_key_login_401(db_client: httpx.AsyncClient, key: str) -> None:
    r = await _login(db_client, "vk_wrong")
    assert r.status_code == 401 and "set-cookie" not in r.headers


async def test_logout_clears_and_rotated_key_invalidates(
    db_client: httpx.AsyncClient, key: str, session: AsyncSession
) -> None:
    cookie = _cookie(await _login(db_client, key))
    assert (await db_client.delete("/v1/session", headers={"cookie": cookie})).status_code == 403  # CSRF
    out = await db_client.delete("/v1/session", headers={"cookie": cookie, **CSRF})
    assert out.status_code == 204
    sc = out.headers["set-cookie"]
    assert "vault_session=" in sc and "max-age=0" in sc.lower()
    # stateless tokens: rotating the user's key invalidates every outstanding session
    await session.execute(update(User).where(User.email == "alice@vault.local").values(api_key_hash="0" * 64))
    assert (await db_client.get(WHOAMI, headers={"cookie": cookie})).status_code == 401


async def test_expired_token_rejected(
    db_client: httpx.AsyncClient, key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.core.auth as auth

    monkeypatch.setattr(auth, "_now", lambda: 1_000)
    cookie = _cookie(await _login(db_client, key))
    monkeypatch.setattr(auth, "_now", lambda: 1_000 + 10**7)
    assert (await db_client.get(WHOAMI, headers={"cookie": cookie})).status_code == 401
