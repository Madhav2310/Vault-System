"""AC1-AC3: the OpenAPI schema documents every route, types every error and declares auth."""

from typing import Any

import pytest

from app.main import create_app

METHODS = ("get", "post", "put", "patch", "delete")
JSON_TYPES = ("application/json",)


@pytest.fixture(scope="module")
def spec() -> dict[str, Any]:
    return create_app().openapi()


def _ops(spec: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    return [(m.upper(), p, item[m]) for p, item in spec["paths"].items() for m in METHODS if m in item]


def _resolve(schema: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    ref = schema.get("$ref")
    return spec["components"]["schemas"][ref.rsplit("/", 1)[1]] if ref else schema


def _has_example(media: dict[str, Any], spec: dict[str, Any]) -> bool:
    if "example" in media or media.get("examples"):
        return True
    schema = _resolve(media.get("schema", {}), spec)
    return bool(schema.get("examples") or schema.get("example"))


def _typed(media: dict[str, Any], media_type: str, spec: dict[str, Any]) -> bool:
    """A named object schema with declared properties; non-JSON bodies (files, SSE) declare a scalar type."""
    schema = media.get("schema")
    if not schema:
        return False
    if media_type not in JSON_TYPES:
        return "type" in schema
    s = _resolve(schema, spec)
    return bool(s.get("properties"))


def test_every_route_documented(spec: dict[str, Any]) -> None:
    ops = _ops(spec)
    assert len(ops) >= 20
    problems: list[str] = []
    for method, path, op in ops:
        where = f"{method} {path}"
        if not op.get("summary"):
            problems.append(f"{where}: no summary")
        if not op.get("description"):
            problems.append(f"{where}: no description")
        ok = {c: r for c, r in op["responses"].items() if c.startswith("2")}
        if not ok:
            problems.append(f"{where}: no 2xx response")
        for code, resp in ok.items():
            content = resp.get("content")
            if code == "204" and not content:
                continue
            if not content:
                problems.append(f"{where} {code}: no body schema")
                continue
            for media_type, media in content.items():
                if not _typed(media, media_type, spec):
                    problems.append(f"{where} {code} {media_type}: untyped schema")
        if not any(_has_example(m, spec) for r in ok.values() for m in r.get("content", {}).values()) and set(
            ok
        ) != {"204"}:
            problems.append(f"{where}: no 2xx example")
    assert not problems, "\n".join(problems)


def test_errors_typed(spec: dict[str, Any]) -> None:
    assert "ErrorResponse" in spec["components"]["schemas"]
    assert "HTTPValidationError" not in spec["components"]["schemas"]
    problems: list[str] = []
    seen = 0
    for method, path, op in _ops(spec):
        for code, resp in op["responses"].items():
            if int(code) < 400:
                continue
            seen += 1
            ref = resp.get("content", {}).get("application/problem+json", {}).get("schema", {}).get("$ref")
            if ref != "#/components/schemas/ErrorResponse":
                problems.append(f"{method} {path} {code}: {ref}")
    assert seen > 20
    assert not problems, "\n".join(problems)


def test_security_scheme(spec: dict[str, Any]) -> None:
    scheme = spec["components"]["securitySchemes"]["ApiKey"]
    assert scheme["type"] == "http" and scheme["scheme"] == "bearer"
    problems: list[str] = []
    for method, path, op in _ops(spec):
        health = path in ("/healthz", "/readyz")
        want: list[Any] = (
            []
            if health or (method, path) == ("POST", "/v1/session")
            else [{"ApiKey": []}, {"SessionCookie": []}]
        )
        if op.get("security") != want:
            problems.append(f"{method} {path}: security={op.get('security')}")
        if not health and "401" not in op["responses"]:
            problems.append(f"{method} {path}: no 401")
    assert not problems, "\n".join(problems)
    assert {"/healthz", "/readyz"} <= set(spec["paths"])


def test_tag_order(spec: dict[str, Any]) -> None:
    assert [t["name"] for t in spec["tags"]] == [
        "Documents",
        "Intelligence",
        "Chat",
        "Runs",
        "Metrics",
        "Session",
        "Health",
    ]
    used = {t for _, _, op in _ops(spec) for t in op["tags"]}
    assert used <= {t["name"] for t in spec["tags"]}
