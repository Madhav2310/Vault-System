"""S21.09 AC2: replaying every POST with the same idempotency key / client_message_id returns the same
resource and creates no duplicate rows (design §14). The POST list comes from OpenAPI: a new POST route fails
`test_every_post` until a replay case is added here."""

import json
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.chat.ledger import persist_turn
from tests.integration.security.conftest import SummaryQueue
from tests.support.docs import seed_document
from tests.support.ledger_env import make_world, record
from tests.support.turn_env import answer_script, install_runner, seed_chunk
from tests.support.uploads import VALID_PDF, docx_bytes, multipart

Sm = async_sessionmaker[AsyncSession]
OWNED_TABLES = (
    "documents",
    "document_versions",
    "processing_jobs",
    "chat_sessions",
    "chat_messages",
    "rag_runs",
    "document_insights",
    "feedback",
    "llm_calls",
)


async def _counts(sm: Sm, owner_id: uuid.UUID) -> dict[str, int]:
    async with sm() as s:
        return {
            t: (
                await s.execute(text(f"SELECT count(*) FROM {t} WHERE owner_id = :o"), {"o": owner_id})
            ).scalar_one()
            for t in OWNED_TABLES
        }


def _delta(after: dict[str, int], before: dict[str, int]) -> dict[str, int]:
    return {t: after[t] - before[t] for t in after if after[t] != before[t]}


async def _twice(
    send: Callable[[], Awaitable[httpx.Response]], sm: Sm, owner_id: uuid.UUID
) -> tuple[httpx.Response, httpx.Response, dict[str, int]]:
    """Send the same request twice; return both responses and the rows created by the FIRST one only
    (asserting the replay added none)."""
    before = await _counts(sm, owner_id)
    first = await send()
    mid = await _counts(sm, owner_id)
    again = await send()
    after = await _counts(sm, owner_id)
    assert _delta(after, mid) == {}, f"replay created rows: {_delta(after, mid)}"
    return first, again, _delta(mid, before)


# --- one case per POST route ---


async def case_documents(ctx: "Ctx") -> None:
    body, h = multipart({}, ("a.pdf", VALID_PDF.read_bytes()))

    def send() -> Awaitable[httpx.Response]:
        return ctx.api.post(
            "/v1/documents", content=body, headers={**h, **ctx.o.auth, "Idempotency-Key": "k-doc"}
        )

    first, again, made = await _twice(send, ctx.sm, ctx.o.id)
    assert first.status_code == again.status_code == 202
    assert first.json() == again.json()
    assert made["documents"] == made["document_versions"] == made["processing_jobs"] == 1
    assert len(ctx.enqueued.calls) == 1  # the replay did not enqueue the job again


async def case_versions(ctx: "Ctx") -> None:
    doc = await seed_document(ctx.sm, ctx.o.id, content=VALID_PDF.read_bytes(), store=ctx.store)
    body, h = multipart({}, ("v2.docx", docx_bytes("second version")))
    url = f"/v1/documents/{doc.document_id}/versions"

    def send() -> Awaitable[httpx.Response]:
        return ctx.api.post(url, content=body, headers={**h, **ctx.o.auth, "Idempotency-Key": "k-ver"})

    first, again, made = await _twice(send, ctx.sm, ctx.o.id)
    assert first.status_code == again.status_code == 202, (first.text, again.text)
    assert first.json() == again.json()
    assert made["document_versions"] == 1 and made.get("documents", 0) == 0


async def case_summaries(ctx: "Ctx") -> None:
    doc = await seed_document(ctx.sm, ctx.o.id)
    url = f"/v1/documents/{doc.document_id}/summaries"

    def send() -> Awaitable[httpx.Response]:
        return ctx.api.post(url, json={"length": "short"}, headers={**ctx.o.auth, "Idempotency-Key": "k-sum"})

    first, again, made = await _twice(send, ctx.sm, ctx.o.id)
    assert first.status_code == again.status_code == 202
    assert first.json() == again.json()  # same summary id
    assert made["document_insights"] == 1
    assert len(ctx.summary_queue.ids) == 1  # one task, not two


async def case_sessions(ctx: "Ctx") -> None:
    def send() -> Awaitable[httpx.Response]:
        return ctx.api.post(
            "/v1/chat/sessions", json={"title": "t"}, headers={**ctx.o.auth, "Idempotency-Key": "k-ses"}
        )

    first, again, made = await _twice(send, ctx.sm, ctx.o.id)
    assert first.status_code == again.status_code == 201
    assert first.json() == again.json()
    assert made["chat_sessions"] == 1
    # the same key with a different body is refused, not silently reused
    r = await ctx.api.post(
        "/v1/chat/sessions", json={"title": "other"}, headers={**ctx.o.auth, "Idempotency-Key": "k-ses"}
    )
    assert r.status_code == 422 and r.json()["code"] == "idempotency_key_reused"
    # a failed create stores no key: the same key works once the request is valid
    bad = await ctx.api.post(
        "/v1/chat/sessions",
        json={"document_ids": [str(uuid.uuid4())]},
        headers={**ctx.o.auth, "Idempotency-Key": "k-ses-bad"},
    )
    assert bad.status_code == 404
    async with ctx.sm() as s:
        n = (
            await s.execute(text("SELECT count(*) FROM idempotency_keys WHERE key = 'k-ses-bad'"))
        ).scalar_one()
    assert n == 0


async def case_messages(ctx: "Ctx") -> None:
    # a turn that is still running (the stub runner never finishes it), then a finished one
    sid = uuid.UUID((await ctx.api.post("/v1/chat/sessions", json={}, headers=ctx.o.auth)).json()["id"])
    payload = {"content": "What is the term?", "client_message_id": "cm-1"}

    def send() -> Awaitable[httpx.Response]:
        return ctx.api.post(f"/v1/chat/sessions/{sid}/messages", json=payload, headers=ctx.o.auth)

    first, again, made = await _twice(send, ctx.sm, ctx.o.id)
    assert first.status_code == again.status_code == 202, (first.text, again.text)
    a, b = first.json(), again.json()
    assert (a["run_id"], a["user_message_id"]) == (b["run_id"], b["user_message_id"])
    assert b["deduplicated"] is True and made["rag_runs"] == 1 and made["chat_messages"] == 1
    # the same id with different content is refused
    r = await ctx.api.post(
        f"/v1/chat/sessions/{sid}/messages",
        json={"content": "Something else", "client_message_id": "cm-1"},
        headers=ctx.o.auth,
    )
    assert r.status_code == 422 and r.json()["code"] == "idempotency_key_reused"


async def case_messages_finished(ctx: "Ctx") -> None:
    """A finished turn: the retry returns the stored answer and makes no model call and no rows."""
    q = "Services twelve months"
    await seed_chunk(ctx.sm, ctx.o.id, "Services Agreement")
    prov = install_runner(
        ctx.api, ctx.sm, answer_script(q, ("Twelve months.", ["E1.0"]), ("Thirty days notice.", ["E1.1"]))
    )
    sid = uuid.UUID((await ctx.api.post("/v1/chat/sessions", json={}, headers=ctx.o.auth)).json()["id"])

    def send() -> Awaitable[httpx.Response]:
        return ctx.api.post(
            f"/v1/chat/sessions/{sid}/messages",
            json={"content": q, "client_message_id": "cm-fin"},
            headers=ctx.o.auth,
        )

    first = await send()
    assert first.status_code == 200, first.text
    calls = len(prov.requests)
    mid = await _counts(ctx.sm, ctx.o.id)
    again = await send()
    assert again.status_code == 200 and again.json()["deduplicated"] is True
    assert again.json()["message"] == first.json()["message"]
    assert again.json()["run_id"] == first.json()["run_id"]
    assert _delta(await _counts(ctx.sm, ctx.o.id), mid) == {}
    assert len(prov.requests) == calls, "a replay must not call the model again"


async def case_feedback(ctx: "Ctx") -> None:
    w = await make_world(ctx.sm, ctx.o)
    async with ctx.sm() as s:
        mid = (await persist_turn(s, ctx.o.id, record(w))).assistant_message_id
    payload = {"message_id": str(mid), "rating": "down", "reason": "wrong"}

    def send() -> Awaitable[httpx.Response]:
        return ctx.api.post("/v1/feedback", json=payload, headers=ctx.o.auth)

    first, again, made = await _twice(send, ctx.sm, ctx.o.id)
    assert first.status_code == again.status_code == 200, (first.text, again.text)
    assert first.json() == again.json() and made["feedback"] == 1


CASES: dict[str, Callable[["Ctx"], Awaitable[None]]] = {
    "POST /v1/documents": case_documents,
    "POST /v1/documents/{document_id}/versions": case_versions,
    "POST /v1/documents/{document_id}/summaries": case_summaries,
    "POST /v1/chat/sessions": case_sessions,
    "POST /v1/chat/sessions/{session_id}/messages": case_messages,
    "POST /v1/feedback": case_feedback,
}


class Ctx:
    def __init__(
        self,
        api: httpx.AsyncClient,
        sm: Sm,
        store: Any,
        enqueued: Any,
        summary_queue: SummaryQueue,
        o: Any,
    ) -> None:
        self.api, self.sm, self.store = api, sm, store
        self.enqueued, self.summary_queue, self.o = enqueued, summary_queue, o


async def test_every_post(
    api: httpx.AsyncClient,
    sm: Sm,
    store: Any,
    enqueued: Any,
    summary_queue: SummaryQueue,
    sec_owners: Callable[[], Any],
) -> None:
    spec = (await api.get("/openapi.json")).json()
    posts = {f"POST {p}" for p, ops in spec["paths"].items() if "post" in ops}
    assert posts == set(CASES), (
        f"POST routes without a replay case: {sorted(posts - set(CASES))}; "
        f"cases for routes that no longer exist: {sorted(set(CASES) - posts)}"
    )
    for route, case in CASES.items():
        o = await sec_owners()  # a fresh owner per route: counts and keys are not shared
        try:
            await case(Ctx(api, sm, store, enqueued, summary_queue, o))
        except AssertionError as e:
            raise AssertionError(f"{route}: {e}") from e


async def test_finished_turn_replay(
    api: httpx.AsyncClient,
    sm: Sm,
    store: Any,
    enqueued: Any,
    summary_queue: SummaryQueue,
    sec_owners: Callable[[], Any],
) -> None:
    await case_messages_finished(Ctx(api, sm, store, enqueued, summary_queue, await sec_owners()))


async def test_idempotency_key_is_scoped_to_owner_on_sessions(
    api: httpx.AsyncClient, sec_owners: Callable[[], Any]
) -> None:
    """Another user's key (same string) must not replay my session to them."""
    a, b = await sec_owners(), await sec_owners()
    ra = await api.post("/v1/chat/sessions", json={}, headers={**a.auth, "Idempotency-Key": "shared"})
    rb = await api.post("/v1/chat/sessions", json={}, headers={**b.auth, "Idempotency-Key": "shared"})
    assert ra.status_code == rb.status_code == 201
    assert ra.json()["id"] != rb.json()["id"]
    assert json.loads(rb.text)["id"] not in ra.text
