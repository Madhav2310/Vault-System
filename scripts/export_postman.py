"""Generate the Postman collection from the app's OpenAPI schema (S18.11).

    cd backend && uv run --frozen python ../scripts/export_postman.py   # -> docs/api/
    cd backend && uv run --frozen python ../scripts/export_postman.py --out -    # stdout

Folders follow the tag order, one request per operation, bearer auth from the `{{api_key}}` variable. Request
bodies are synthesized from the schema (its example when it has one). The collection is a pure function of the
schema (`build_collection`), so regenerating on an unchanged API produces an identical file.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "docs" / "api" / "vault.postman_collection.json"
SCHEMA_URL = "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"
METHODS = ("get", "post", "put", "patch", "delete")

_SAMPLE_BY_TYPE: dict[str, Any] = {"string": "string", "integer": 0, "number": 0, "boolean": False}


def _resolve(schema: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    ref = schema.get("$ref")
    if not ref:
        return schema
    node: Any = spec
    for part in ref.removeprefix("#/").split("/"):
        node = node[part]
    return dict(node)


def sample(schema: dict[str, Any], spec: dict[str, Any], depth: int = 0) -> Any:
    """A representative JSON value for a schema: its example, else defaults by type."""
    s = _resolve(schema, spec)
    if "example" in s:
        return s["example"]
    if s.get("examples"):
        return s["examples"][0]
    if "default" in s and s["default"] is not None:
        return s["default"]
    if s.get("enum"):
        return s["enum"][0]
    for key in ("anyOf", "oneOf"):
        options = [o for o in s.get(key, []) if o.get("type") != "null"]
        if options:
            return sample(options[0], spec, depth)
    if depth > 4:
        return None
    t = s.get("type")
    if t == "object" or "properties" in s:
        props = s.get("properties", {})
        required = set(s.get("required", []))
        return {k: sample(v, spec, depth + 1) for k, v in props.items() if k in required or depth == 0}
    if t == "array":
        return [sample(s["items"], spec, depth + 1)] if "items" in s else []
    if s.get("format") == "uuid":
        return "00000000-0000-0000-0000-000000000000"
    return _SAMPLE_BY_TYPE.get(t or "")


def _request(method: str, path: str, op: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    pc_path = re.sub(r"\{([^}]+)\}", r":\1", path)
    params = [_resolve(p, spec) for p in op.get("parameters", [])]
    query = [
        {"key": p["name"], "value": "", "disabled": not p.get("required", False)}
        for p in params
        if p["in"] == "query"
    ]
    variables = [
        {"key": p["name"], "value": str(sample(p.get("schema", {}), spec) or "")}
        for p in params
        if p["in"] == "path"
    ]
    headers = [{"key": p["name"], "value": "", "disabled": True} for p in params if p["in"] == "header"]
    url: dict[str, Any] = {
        "raw": "{{baseUrl}}" + pc_path + ("?" + "&".join(f"{q['key']}=" for q in query) if query else ""),
        "host": ["{{baseUrl}}"],
        "path": [p for p in pc_path.split("/") if p],
    }
    if query:
        url["query"] = query
    if variables:
        url["variable"] = variables
    req: dict[str, Any] = {"method": method.upper(), "header": headers, "url": url}
    description = op.get("description") or op.get("summary") or ""
    if description:
        req["description"] = description
    content = (op.get("requestBody") or {}).get("content", {})
    if "application/json" in content:
        body = sample(content["application/json"].get("schema", {}), spec)
        req["header"] = [*headers, {"key": "Content-Type", "value": "application/json"}]
        req["body"] = {
            "mode": "raw",
            "raw": json.dumps(body, indent=2),
            "options": {"raw": {"language": "json"}},
        }
    elif "multipart/form-data" in content:
        props = _resolve(content["multipart/form-data"].get("schema", {}), spec).get("properties", {})
        req["body"] = {
            "mode": "formdata",
            "formdata": [
                {"key": k, "type": "file", "src": []}
                if v.get("format") == "binary"
                else {"key": k, "type": "text", "value": "", "disabled": True}
                for k, v in props.items()
            ],
        }
    if op.get("security") == []:
        req["auth"] = {"type": "noauth"}
    return req


def build_collection(spec: dict[str, Any]) -> dict[str, Any]:
    tag_order = [t["name"] for t in spec.get("tags", [])]
    folders: dict[str, list[dict[str, Any]]] = {t: [] for t in tag_order}
    for path, ops in spec["paths"].items():
        for method in METHODS:
            op = ops.get(method)
            if op is None:
                continue
            tag = (op.get("tags") or ["Other"])[0]
            folders.setdefault(tag, []).append(
                {
                    "name": f"{op.get('summary', path)} ({method.upper()} {path})",
                    "request": _request(method, path, op, spec),
                    "response": [],
                }
            )
    info = spec["info"]
    return {
        "info": {
            "name": info["title"],
            "description": f"{info['title']} API {info['version']}. Generated by scripts/export_postman.py.",
            "schema": SCHEMA_URL,
        },
        "auth": {
            "type": "bearer",
            "bearer": [{"key": "token", "value": "{{api_key}}", "type": "string"}],
        },
        "variable": [
            {"key": "baseUrl", "value": "http://localhost:8100"},
            {"key": "api_key", "value": "vk_replace_me"},
        ],
        "item": [{"name": name, "item": items} for name, items in folders.items() if items],
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    p.add_argument("--out", default=str(DEFAULT_OUT), help="output path, or - for stdout")
    a = p.parse_args(argv)
    from app.main import create_app  # needs backend/ on the path: run from backend/

    text = json.dumps(build_collection(create_app().openapi()), indent=2) + "\n"
    if a.out == "-":
        sys.stdout.write(text)
    else:
        out = pathlib.Path(a.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text)
        print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
