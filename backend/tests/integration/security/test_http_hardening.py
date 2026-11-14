"""Path traversal on the SPA mount (`/app`), header injection (request id, Idempotency-Key, download
filename), and credential handling. Raw ASGI calls where an HTTP client would normalise the attack away."""

import asyncio
import pathlib
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.main import create_app
from tests.support.uploads import VALID_PDF, multipart


async def asgi(
    app: Any, method: str, path: str, headers: list[tuple[bytes, bytes]], body: bytes = b""
) -> tuple[int, dict[bytes, bytes], bytes]:
    """One request straight into the ASGI app with the path and headers exactly as given."""
    sent = False
    never = asyncio.Event()  # the client stays connected until the response is complete
    status, rh, chunks = 0, {}, []

    async def receive() -> dict[str, Any]:
        nonlocal sent
        if sent:
            await never.wait()
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(m: dict[str, Any]) -> None:
        nonlocal status, rh
        if m["type"] == "http.response.start":
            status, rh = m["status"], {k.lower(): v for k, v in m["headers"]}
        elif m["type"] == "http.response.body":
            chunks.append(m.get("body", b""))

    raw, _, qs = path.partition("?")
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,
        "scheme": "http", "path": raw, "raw_path": raw.encode(), "query_string": qs.encode(),
        "headers": headers, "client": ("127.0.0.1", 1), "server": ("test", 80),
    }  # fmt: skip
    await app(scope, receive, send)
    return status, rh, b"".join(chunks)


@pytest.fixture
def ui(tmp_path: pathlib.Path) -> tuple[Any, pathlib.Path]:
    d = tmp_path / "dist"
    (d / "assets").mkdir(parents=True)
    (d / "index.html").write_text("<!doctype html><title>Vault</title><div id=root></div>")
    (tmp_path / "secret.txt").write_text("TOP-SECRET-OUTSIDE")
    (d / "leak").symlink_to(tmp_path / "secret.txt")  # a symlink inside dist pointing outside it
    return create_app(ui_dist=d), tmp_path


@pytest.mark.parametrize(
    "path",
    [
        "/app/../secret.txt",
        "/app/../../secret.txt",
        "/app/assets/../../secret.txt",
        "/app/..%2fsecret.txt",
        "/app/%2e%2e/secret.txt",
        "/app/%252e%252e%252fsecret.txt",
        "/app/..\\secret.txt",
        "/app/....//secret.txt",
        "/app//etc/passwd",
        "/app/%00../secret.txt",
        "/app/leak",
        "/app/../../../../../../etc/passwd",
    ],
)
async def test_app_path_traversal_never_serves_outside_the_build(
    ui: tuple[Any, pathlib.Path], path: str
) -> None:
    app, _ = ui
    status, _, body = await asgi(app, "GET", path, [])
    assert b"TOP-SECRET-OUTSIDE" not in body and b"root:" not in body, path
    assert status in (200, 400, 404), (path, status)
    if status == 200:
        assert b"<div id=root>" in body  # the SPA fallback, nothing else


async def test_app_path_traversal_via_http_client(ui: tuple[Any, pathlib.Path]) -> None:
    app, _ = ui
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        for p in ("/app/%2e%2e%2fsecret.txt", "/app/%2e%2e/%2e%2e/secret.txt", "/app/leak"):
            assert "TOP-SECRET-OUTSIDE" not in (await c.get(p)).text, p


async def test_hostile_request_id_is_replaced_not_reflected(api: httpx.AsyncClient) -> None:
    app = api._transport.app  # type: ignore[attr-defined]
    for evil in (b"abc\r\nX-Injected: 1", b"a b", b"<script>alert(1)</script>", b"x" * 500, b"id;rm -rf /"):
        status, headers, _ = await asgi(app, "GET", "/healthz", [(b"x-request-id", evil)])
        rid = headers[b"x-request-id"]
        assert status == 200 and rid != evil and b"\r" not in rid and b"\n" not in rid and len(rid) == 32
        assert b"x-injected" not in headers
    status, headers, _ = await asgi(app, "GET", "/healthz", [(b"x-request-id", b"good-id_1.2")])
    assert headers[b"x-request-id"] == b"good-id_1.2"


async def test_idempotency_key_header_validation(
    api: httpx.AsyncClient, make_owner: Callable[[], Any]
) -> None:
    o = await make_owner()
    app = api._transport.app  # type: ignore[attr-defined]
    body, h = multipart({}, ("a.pdf", VALID_PDF.read_bytes()))
    base = [
        (b"content-type", h["Content-Type"].encode()),
        (b"authorization", o.auth["Authorization"].encode()),
    ]
    for key in (b"", b"k" * 256, b"bell\x07key", b"zero\xe2\x80\x8bwidth", b"line\x0bfeed"):
        status, _, out = await asgi(app, "POST", "/v1/documents", [*base, (b"idempotency-key", key)], body)
        assert status == 422, (key, status, out[:200])


async def test_download_headers_cannot_be_injected_through_the_filename(
    api: httpx.AsyncClient, make_owner: Callable[[], Any]
) -> None:
    o = await make_owner()
    name = "x%0d%0aX-Evil: 1;filename=evil.exe\u202e\u2028<script>.pdf"
    body, h = multipart({}, (name, VALID_PDF.read_bytes()))
    up = await api.post("/v1/documents", content=body, headers={**h, **o.auth})
    assert up.status_code == 202, up.text
    r = await api.get(f"/v1/versions/{up.json()['version_id']}/file", headers=o.auth)
    assert r.status_code == 200
    assert "x-evil" not in {k.lower() for k in r.headers}
    cd = r.headers["content-disposition"]
    assert cd.startswith("inline; filename*=UTF-8''") and '"' not in cd and "\r" not in cd and "\n" not in cd
    assert r.headers["content-type"] == "application/pdf"  # the sniffed type, not client-declared


@pytest.mark.parametrize(
    "auth",
    [
        "",
        "Bearer",
        "Bearer ",
        "Bearer nope",
        "Basic dXNlcjpwYXNz",
        "Bearer vk_" + "A" * 5000,
        "bearer  vk_x",
        "Bearer vk_x' OR '1'='1",
        "Bearer vk_x\x00",
    ],
)
async def test_bad_credentials_are_401(api: httpx.AsyncClient, auth: str) -> None:
    app = api._transport.app  # type: ignore[attr-defined]
    hdrs = [(b"authorization", auth.encode("latin-1"))] if auth else []
    status, headers, _ = await asgi(app, "GET", "/v1/documents", hdrs)
    assert status == 401 and headers[b"www-authenticate"] == b"Bearer"


async def test_api_key_in_query_string_is_not_accepted(
    api: httpx.AsyncClient, make_owner: Callable[[], Any]
) -> None:
    o = await make_owner()
    key = o.key
    for q in (f"?api_key={key}", f"?access_token={key}", f"?authorization=Bearer%20{key}"):
        assert (await api.get("/v1/documents" + q)).status_code == 401


async def test_errors_do_not_leak_internals(api: httpx.AsyncClient, make_owner: Callable[[], Any]) -> None:
    o = await make_owner()
    for path in (
        "/v1/documents/not-a-uuid",
        "/v1/chunks/%27%20OR%201=1--",
        "/v1/runs/00000000-0000-0000-0000-000000000000",
    ):
        r = await api.get(path, headers=o.auth)
        assert r.status_code in (404, 422)
        low = r.text.lower()
        for needle in ("traceback", "sqlalchemy", "asyncpg", "/users/", "/app/", ".py"):
            assert needle not in low, (path, needle)
