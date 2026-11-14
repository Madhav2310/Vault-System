"""F-chat-regenerate-history: a turn that `replaces` an earlier one hides it from history once done."""

import uuid
from collections.abc import Callable
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.support.ledger_env import ledger_owners
from tests.support.turn_env import answer_script, install_runner, new_session, post, seed_chunk

__all__ = ["ledger_owners"]  # fixture import

Q = "Services twelve months"
ONE = (("Twelve months.", ["E1.0"]),)


async def items(api: httpx.AsyncClient, owner: Any, sid: uuid.UUID) -> list[dict[str, Any]]:
    r = await api.get(f"/v1/chat/sessions/{sid}/messages", headers=owner.auth)
    return list(r.json()["items"])


async def test_regenerate_replaces_the_old_pair(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    o = await ledger_owners()
    await seed_chunk(sm, o.id)
    install_runner(api, sm, [*answer_script(Q, *ONE), *answer_script(Q, *ONE)])
    sid = await new_session(api, o)
    first = (await post(api, o, sid, Q, "m1")).json()["message"]
    assert len(await items(api, o, sid)) == 2

    # Regenerate: reask_of the answer, replacing it
    r = await api.post(
        f"/v1/chat/sessions/{sid}/messages",
        json={"reask_of": first["id"], "replaces": first["id"], "client_message_id": "m2"},
        headers=o.auth,
    )
    assert r.status_code == 200 and r.json()["message"]["id"] != first["id"]
    shown = await items(api, o, sid)
    assert [m["role"] for m in shown] == ["user", "assistant"]
    assert shown[-1]["id"] == r.json()["message"]["id"]  # only the new pair is shown


async def test_without_replaces_both_turns_stay(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    o = await ledger_owners()
    await seed_chunk(sm, o.id)
    install_runner(api, sm, [*answer_script(Q, *ONE), *answer_script(Q, *ONE)])
    sid = await new_session(api, o)
    await post(api, o, sid, Q, "m1")
    await post(api, o, sid, Q, "m2")
    assert len(await items(api, o, sid)) == 4


async def test_replaces_is_owner_scoped(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    o, other = await ledger_owners(), await ledger_owners()
    await seed_chunk(sm, o.id)
    install_runner(api, sm, answer_script(Q, *ONE))
    sid = await new_session(api, o)
    first = (await post(api, o, sid, Q, "m1")).json()["message"]
    body = {"content": Q, "replaces": first["id"], "client_message_id": "x1"}
    sess = f"/v1/chat/sessions/{sid}/messages"
    assert (await api.post(sess, json=body, headers=other.auth)).status_code == 404  # not their session
    unknown = {**body, "replaces": str(uuid.uuid4()), "client_message_id": "x2"}
    assert (await api.post(sess, json=unknown, headers=o.auth)).status_code == 404
