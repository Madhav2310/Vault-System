import asyncio
import io
import json

import httpx
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider


def _lines(buf: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in buf.getvalue().splitlines() if line.strip()]


async def test_request_log_has_ids(client: httpx.AsyncClient, log_stream: io.StringIO) -> None:
    tracer = TracerProvider().get_tracer("test")
    with tracer.start_as_current_span("client-request"):
        r = await client.get("/_test/log/a")
    assert r.status_code == 200
    access = [ln for ln in _lines(log_stream) if ln["event"] == "request"]
    assert len(access) == 1
    assert access[0]["request_id"] == r.headers["x-request-id"]
    assert len(str(access[0]["trace_id"])) == 32
    assert access[0]["route"] == "/_test/log/{tag}"


async def test_request_id_echo(client: httpx.AsyncClient) -> None:
    r = await client.get("/healthz", headers={"X-Request-ID": "abc-123"})
    assert r.headers["x-request-id"] == "abc-123"
    bad = await client.get("/healthz", headers={"X-Request-ID": "bad id\n<script>"})
    assert bad.headers["x-request-id"] != "bad id\n<script>" and len(bad.headers["x-request-id"]) == 32


async def test_context_isolation_concurrent(client: httpx.AsyncClient, log_stream: io.StringIO) -> None:
    tags = [f"t{i}" for i in range(20)]
    resps = await asyncio.gather(
        *(
            client.get(
                f"/_test/log/{t}", params={"delay": 0.01 * (i % 5)}, headers={"X-Request-ID": f"req-{t}"}
            )
            for i, t in enumerate(tags)
        )
    )
    assert all(r.status_code == 200 for r in resps)
    probes = [ln for ln in _lines(log_stream) if ln["event"] == "probe"]
    assert len(probes) == 20
    for p in probes:
        assert p["request_id"] == f"req-{p['tag']}"
    assert trace.get_current_span().get_span_context().is_valid is False
