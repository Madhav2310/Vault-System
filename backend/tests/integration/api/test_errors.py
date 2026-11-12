import httpx

PROBLEM_KEYS = {"type", "title", "status", "code", "detail", "retryable"}


async def test_404_problem_json(client: httpx.AsyncClient) -> None:
    r = await client.get("/no/such/route")
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/problem+json")
    body = r.json()
    assert body.keys() >= PROBLEM_KEYS and body["code"] == "not_found" and body["retryable"] is False


async def test_422_problem_json(client: httpx.AsyncClient) -> None:
    r = await client.post("/_test/body", json={"content": "confidential clause text", "n": "not-int"})
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "validation_error" and body["status"] == 422
    assert "confidential clause text" not in r.text and "not-int" not in r.text  # values never echoed


async def test_500_no_stacktrace(client: httpx.AsyncClient) -> None:
    r = await client.get("/_test/boom")
    assert r.status_code == 500
    body = r.json()
    assert body["code"] == "internal_error" and body["detail"] is None
    for leak in ("Traceback", "secret internal detail", "/srv/app", "RuntimeError"):
        assert leak not in r.text
