"""`GET /v1/runs/{id}` (S18.03): shape, owner scoping, template explanations, leak scan, running state."""

import json
import uuid
from collections.abc import Callable
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.settings import get_settings
from tests.integration.chat.conftest import owners
from tests.support.ledger_env import ledger_owners
from tests.support.turn_env import (
    EMBED_SLOT,
    analysis,
    answer_script,
    install_runner,
    new_session,
    post,
    seed_chunk,
)

__all__ = ["ledger_owners", "owners"]  # fixture imports

Q = "Services twelve months"


async def _answered_run(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> tuple[Any, uuid.UUID, str]:
    o = await ledger_owners()
    await seed_chunk(sm, o.id, "Consulting Agreement")
    install_runner(api, sm, answer_script(Q, ("Twelve months.", ["E1.0"]), ("Thirty days notice.", ["E1.1"])))
    sid = await new_session(api, o)
    r = await post(api, o, sid, Q, "m1")
    assert r.status_code == 200, r.text
    return o, sid, r.json()["run_id"]


async def test_shape_and_owner(
    sm: async_sessionmaker[AsyncSession],
    ledger_owners: Callable[[], Any],
    api: httpx.AsyncClient,
) -> None:
    o, _, run_id = await _answered_run(sm, ledger_owners, api)
    r = await api.get(f"/v1/runs/{run_id}", headers=o.auth)
    assert r.status_code == 200, r.text
    v = r.json()
    assert set(v) == {
        "run_id", "status", "mode", "route", "response_type", "reason_codes", "explanation",
        "sources", "steps", "usage", "message",
    }  # fmt: skip
    assert (v["status"], v["mode"], v["route"], v["response_type"]) == ("completed", "rag", "qa", "answer")
    assert v["sources"] == [
        {"document_title": "Consulting Agreement", "version_no": 1, "pages": [3, 3], "chunks_used": 1}
    ]
    nodes = [s["node"] for s in v["steps"]]
    assert (
        nodes[0] == "load_context" and "generate" in nodes and nodes[-1] == "verify"
    )  # persist writes the steps, so it is not among them
    assert all(set(s) == {"node", "status", "duration_ms"} for s in v["steps"])
    assert v["usage"]["calls"] == 2 and v["usage"]["input_tokens"] > 0 and v["usage"]["cost_usd"] >= 0
    assert v["message"]["content"] == "Twelve months. Thirty days notice."
    assert v["message"]["segments"][0]["evidence"][0]["cited_text"].startswith("The Consultant")
    assert v["explanation"] == ["Answered from 1 passage in 'Consulting Agreement' v1."]
    other = await ledger_owners()
    assert (await api.get(f"/v1/runs/{run_id}", headers=other.auth)).status_code == 404
    assert (await api.get(f"/v1/runs/{uuid.uuid4()}", headers=o.auth)).status_code == 404


async def test_explanations(
    sm: async_sessionmaker[AsyncSession],
    ledger_owners: Callable[[], Any],
    api: httpx.AsyncClient,
) -> None:
    o, _, run_id = await _answered_run(sm, ledger_owners, api)
    async with sm() as s:  # make it a full-context run over a document of known size
        await s.execute(text("UPDATE rag_runs SET mode = 'full_context' WHERE id = :r"), {"r": run_id})
        await s.execute(
            text(
                "UPDATE document_versions SET token_count = 18400 WHERE owner_id = :o AND id IN "
                "(SELECT version_id FROM run_versions WHERE run_id = :r)"
            ),
            {"o": o.id, "r": run_id},
        )
        await s.commit()
    v = (await api.get(f"/v1/runs/{run_id}", headers=o.auth)).json()
    assert v["explanation"][0] == "Read the whole document (1 document, 18k tokens)."

    # a gate abstention: documents in scope, nothing retrieved
    o2 = await ledger_owners()
    from tests.support.docs import seed_document

    await seed_document(sm, o2.id, title="Empty")
    install_runner(api, sm, [analysis("anything"), EMBED_SLOT])
    sid = await new_session(api, o2)
    r = await post(api, o2, sid, "anything", "a1")
    assert r.json()["message"]["response_type"] == "abstain"
    v2 = (await api.get(f"/v1/runs/{r.json()['run_id']}", headers=o2.auth)).json()
    assert v2["explanation"] == ["Nothing in your documents matched closely enough."]
    assert v2["sources"] == [] and v2["response_type"] == "abstain" and v2["reason_codes"] == ["NO_EVIDENCE"]


async def test_no_internal_leak(
    sm: async_sessionmaker[AsyncSession],
    ledger_owners: Callable[[], Any],
    api: httpx.AsyncClient,
) -> None:
    o, _, run_id = await _answered_run(sm, ledger_owners, api)
    body = (await api.get(f"/v1/runs/{run_id}", headers=o.auth)).text
    s = get_settings()
    models = {s.model_fast, s.model_balanced, s.model_deep, s.model_flagship}
    for needle in (*models, "gpt-", "answer_rag", "analyze_query", "retrieval@", "trace", "prompt"):
        assert needle not in body, needle
    assert "config" not in json.loads(body)


async def test_running_state(
    sm: async_sessionmaker[AsyncSession],
    ledger_owners: Callable[[], Any],
    api: httpx.AsyncClient,
) -> None:
    o = await ledger_owners()
    await seed_chunk(sm, o.id, "Doc")
    sid = await new_session(api, o)
    r = await post(api, o, sid, Q, "m1")  # the default test runner does nothing: the run stays `running`
    assert r.status_code == 202 and r.json()["status"] == "running"
    v = (await api.get(f"/v1/runs/{r.json()['run_id']}", headers=o.auth)).json()
    assert v["status"] == "running" and v["message"] is None and v["response_type"] is None
    assert v["explanation"] == ["The answer is still being prepared."] and v["steps"] == []
