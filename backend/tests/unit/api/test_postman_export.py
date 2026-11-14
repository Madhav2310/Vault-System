"""The Postman collection generated from the schema contains every route."""

import importlib.util
import pathlib
from types import ModuleType
from typing import Any

from app.main import create_app

REPO = pathlib.Path(__file__).resolve().parents[4]
METHODS = ("get", "post", "put", "patch", "delete")


def _exporter() -> ModuleType:
    spec = importlib.util.spec_from_file_location("export_postman", REPO / "scripts" / "export_postman.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _requests(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for it in items:
        out.extend(_requests(it["item"]) if "item" in it else [it])
    return out


def test_all_routes() -> None:
    spec = create_app().openapi()
    coll = _exporter().build_collection(spec)
    ops = {(m.upper(), p) for p, item in spec["paths"].items() for m in METHODS if m in item}
    assert ops, "the schema has no operations"
    reqs = _requests(coll["item"])
    assert len(reqs) == len(ops)
    names = [r["name"] for r in reqs]
    for method, path in ops:
        assert any(n.endswith(f"({method} {path})") for n in names), f"missing {method} {path}"
    # folders follow the tag order, and auth is the bearer variable
    folders = [f["name"] for f in coll["item"]]
    assert folders == [t["name"] for t in spec["tags"] if t["name"] in folders]
    assert coll["auth"]["bearer"][0]["value"] == "{{api_key}}"
