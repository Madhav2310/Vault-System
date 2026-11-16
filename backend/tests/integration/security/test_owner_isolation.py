"""Every route is covered by a cross-owner test. The route list comes from the app's OpenAPI; a new route that
is neither covered below nor on the justified allowlist fails `test_every_route` loudly.

World: owner A has a ready document (blob, chunk), a custom summary, a session with a finished turn
(run, assistant message). Owner B attacks every A-owned id on every route and must see 404 (or, on list and
metric routes, nothing of A's). Afterwards A's data is unchanged, and A's own requests are not 404 (positive
control, so a route that 404s for everyone cannot pass)."""

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.chat.ledger import persist_turn
from app.storage.local import LocalObjectStore
from tests.support.docs import seed_document
from tests.support.ledger_env import make_world, record
from tests.support.uploads import VALID_PDF, multipart

# Routes with no owner-scoped data. Each entry says why it is safe to leave out of the cross-owner sweep.
ALLOWLIST: dict[str, str] = {
    "GET /healthz": "liveness probe: no auth, no data",
    "GET /readyz": "readiness probe: dependency status only, no owner data",
    "POST /v1/session": "stateless login: returns no owner data, only a cookie bound to the caller's own key",
    "POST /v1/session/local": "local-only sign-in as one fixed user; 404 off localhost; no owner data",
    "GET /v1/session": "credential probe: 204 or 401, no owner data",
    "DELETE /v1/session": "clears the caller's own cookie; touches no resource",
    "GET /v1/chat/events/schema": "static SSE event JSON schema: identical for every caller (asserted below)",
}


@dataclass(frozen=True)
class Spec:
    """How owner B attacks a route. kind: `404` (B must get 404 for A's ids), `list` (200, none of A's ids),
    `own` (B creates its own resource, A's is untouched: checked in a dedicated test)."""

    kind: str
    kwargs: dict[str, Any] = field(default_factory=dict)
    control: bool = True  # A's request must not be 404 (False for streams and destructive routes)


MSG = {"json": {"content": "hello", "client_message_id": "attack-1"}}
COVERED: dict[str, Spec] = {
    "GET /v1/documents": Spec("list"),
    "POST /v1/documents": Spec("own"),
    "GET /v1/documents/{document_id}": Spec("404"),
    "DELETE /v1/documents/{document_id}": Spec("404", control=False),
    "GET /v1/documents/{document_id}/brief": Spec("404"),
    "POST /v1/documents/{document_id}/summaries": Spec("404", {"json": {"length": "short"}}),
    "GET /v1/documents/{document_id}/summaries/{summary_id}": Spec("404"),
    "POST /v1/documents/{document_id}/versions": Spec("404", {"files": True}, control=False),
    "GET /v1/versions/{version_id}/processing": Spec("404"),
    "GET /v1/versions/{version_id}/file": Spec("404"),
    "GET /v1/versions/{version_id}/processing/stream": Spec("404", control=False),
    "GET /v1/chunks/{chunk_id}": Spec("404"),
    "POST /v1/chat/sessions": Spec("404", {"json": {"document_ids": ["{document_id}"]}}, control=False),
    "GET /v1/chat/sessions": Spec("list"),
    "GET /v1/chat/sessions/{session_id}": Spec("404"),
    "GET /v1/chat/sessions/{session_id}/messages": Spec("404"),
    "POST /v1/chat/sessions/{session_id}/messages": Spec("404", MSG, control=False),
    "GET /v1/runs/{run_id}": Spec("404"),
    "GET /v1/metrics/documents": Spec("list"),
    "GET /v1/metrics/processing": Spec("list"),
    "GET /v1/metrics/chat": Spec("list"),
    "POST /v1/feedback": Spec("404", {"json": {"message_id": "{message_id}", "rating": "up"}}, control=False),
}


@dataclass
class World:
    a: Any
    b: Any
    ids: dict[str, str]


async def _world(
    api: httpx.AsyncClient,
    sm: async_sessionmaker[AsyncSession],
    store: LocalObjectStore,
    sec_owners: Callable[[], Any],
) -> World:
    a, b = await sec_owners(), await sec_owners()
    w = await make_world(sm, a)
    async with sm() as s:
        persisted = await persist_turn(s, a.id, record(w))
    doc = await seed_document(sm, a.id, title="A secret", content=VALID_PDF.read_bytes(), store=store)
    # a real custom summary for A's document (through the API, so the row is what production writes)
    r = await api.post(f"/v1/documents/{doc.document_id}/summaries", json={"length": "short"}, headers=a.auth)
    assert r.status_code in (200, 202), r.text
    ids = {
        "document_id": str(doc.document_id),
        "version_id": str(doc.version_id),
        "chunk_id": str(w.chunk_id),
        "session_id": str(w.session_id),
        "run_id": str(w.run_id),
        "message_id": str(persisted.assistant_message_id),
        "summary_id": r.json()["id"],
        "page": "1",
    }
    return World(a, b, ids)


def _fill(obj: Any, ids: dict[str, str]) -> Any:
    if isinstance(obj, str):
        return obj.format(**ids) if "{" in obj else obj
    if isinstance(obj, list):
        return [_fill(x, ids) for x in obj]
    if isinstance(obj, dict):
        return {k: _fill(v, ids) for k, v in obj.items()}
    return obj


async def _call(
    api: httpx.AsyncClient, owner: Any, route: str, spec: Spec, ids: dict[str, str]
) -> httpx.Response:
    method, template = route.split(" ", 1)
    kw = dict(spec.kwargs)
    if kw.pop("files", False):
        body, h = multipart({}, ("a.pdf", VALID_PDF.read_bytes()))
        kw.update(content=body, headers={**h, **owner.auth})
    else:
        kw["headers"] = owner.auth
    return await api.request(method, template.format(**ids), **_fill(kw, ids))


async def _a_state(sm: async_sessionmaker[AsyncSession], a_id: uuid.UUID) -> dict[str, int]:
    async with sm() as s:
        q = {
            "documents": "SELECT count(*) FROM documents WHERE owner_id = :o AND deleted_at IS NULL",
            "versions": "SELECT count(*) FROM document_versions WHERE owner_id = :o",
            "sessions": "SELECT count(*) FROM chat_sessions WHERE owner_id = :o",
            "messages": "SELECT count(*) FROM chat_messages WHERE owner_id = :o",
            "feedback": "SELECT count(*) FROM feedback WHERE owner_id = :o",
            "summaries": "SELECT count(*) FROM document_insights WHERE owner_id = :o",
        }
        return {k: (await s.execute(text(v), {"o": a_id})).scalar_one() for k, v in q.items()}


async def _oas_ops(api: httpx.AsyncClient) -> set[str]:
    spec = (await api.get("/openapi.json")).json()
    methods = {"get", "post", "put", "patch", "delete", "head", "options"}
    return {f"{m.upper()} {p}" for p, ops in spec["paths"].items() for m in ops if m in methods}


async def test_every_route(
    api: httpx.AsyncClient,
    sm: async_sessionmaker[AsyncSession],
    store: LocalObjectStore,
    sec_owners: Callable[[], Any],
) -> None:
    ops = await _oas_ops(api)
    known = set(COVERED) | set(ALLOWLIST)
    assert not ops - known, (
        f"new route(s) with no cross-owner test: {sorted(ops - known)}. Add them to COVERED "
        "(or to ALLOWLIST with a justification) in tests/integration/security/test_owner_isolation.py"
    )
    assert not known - ops, f"covered routes that no longer exist: {sorted(known - ops)}"

    w = await _world(api, sm, store, sec_owners)
    before = await _a_state(sm, w.a.id)
    a_texts = set(w.ids.values()) - {"1"}

    # the allowlisted static route really is the same for everyone
    s_a = await api.get("/v1/chat/events/schema", headers=w.a.auth)
    s_b = await api.get("/v1/chat/events/schema", headers=w.b.auth)
    assert s_a.status_code == s_b.status_code == 200 and s_a.json() == s_b.json()

    for route, spec in COVERED.items():
        if spec.kind == "own":
            continue  # dedicated test below
        r = await _call(api, w.b, route, spec, w.ids)
        if spec.kind == "404":
            assert r.status_code == 404, f"{route}: owner B got {r.status_code}, expected 404: {r.text[:200]}"
        else:
            assert r.status_code == 200, f"{route}: {r.status_code} {r.text[:200]}"
        leaked = {i for i in a_texts if i in r.text}
        assert not leaked, f"{route}: response for owner B contains owner A's ids {sorted(leaked)}"

    # B's attacks changed nothing of A's
    assert await _a_state(sm, w.a.id) == before

    # positive control: the same ids work for A, so the 404s above are ownership, not a dead route
    for route, spec in COVERED.items():
        if spec.kind == "404" and spec.control:
            r = await _call(api, w.a, route, spec, w.ids)
            assert r.status_code != 404, f"{route}: owner A got 404 for its own resource ({r.text[:200]})"

    # unauthenticated requests are refused on every non-allowlisted route
    for route in COVERED:
        method, template = route.split(" ", 1)
        r = await api.request(method, template.format(**w.ids))
        assert r.status_code in (401, 403), f"{route} without credentials: {r.status_code}"


async def test_chat_reask_and_scope_of_foreign_ids(
    api: httpx.AsyncClient,
    sm: async_sessionmaker[AsyncSession],
    store: LocalObjectStore,
    sec_owners: Callable[[], Any],
) -> None:
    """Foreign ids reach the app through bodies, not only paths: reask_of, session scope."""
    w = await _world(api, sm, store, sec_owners)
    r = await api.post("/v1/chat/sessions", json={}, headers=w.b.auth)
    b_sid = r.json()["id"]
    r = await api.post(
        f"/v1/chat/sessions/{b_sid}/messages",
        json={"reask_of": w.ids["message_id"], "client_message_id": "reask-1"},
        headers=w.b.auth,
    )
    assert r.status_code == 404
    r = await api.post(
        "/v1/chat/sessions",
        json={"document_ids": [w.ids["document_id"], str(uuid.uuid4())]},
        headers=w.b.auth,
    )
    assert r.status_code == 404


async def test_post_documents_dedupe_is_per_owner(
    api: httpx.AsyncClient,
    sm: async_sessionmaker[AsyncSession],
    store: LocalObjectStore,
    sec_owners: Callable[[], Any],
) -> None:
    """Identical bytes uploaded by B must not dedupe into A's document or reveal that A has it."""
    a, b = await sec_owners(), await sec_owners()
    body, h = multipart({}, ("a.pdf", VALID_PDF.read_bytes()))
    ra = await api.post("/v1/documents", content=body, headers={**h, **a.auth})
    rb = await api.post("/v1/documents", content=body, headers={**h, **b.auth})
    assert ra.status_code == rb.status_code == 202, (ra.text, rb.text)  # not 200 "deduplicated"
    assert ra.json()["document_id"] != rb.json()["document_id"]
    assert ra.json()["version_id"] != rb.json()["version_id"]
    assert rb.json().get("deduplicated") is False
    listing = (await api.get("/v1/documents", headers=a.auth)).json()
    assert ra.json()["document_id"] in json.dumps(listing) and rb.json()["document_id"] not in json.dumps(
        listing
    )


async def test_metrics_global_scope_is_admin_only(
    api: httpx.AsyncClient, sec_owners: Callable[[], Any]
) -> None:
    o = await sec_owners()
    for path in ("documents", "processing", "chat"):
        r = await api.get(f"/v1/metrics/{path}", params={"scope": "global"}, headers=o.auth)
        assert r.status_code == 403, path
