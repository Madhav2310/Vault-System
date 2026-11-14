"""History redaction for deleted documents (F-042)."""

from collections.abc import Callable
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.chat.ledger import persist_turn
from tests.support.ledger_env import ledger_owners, make_world, record

__all__ = ["ledger_owners"]  # fixture import


async def test_redacted(
    api: httpx.AsyncClient, sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any]
) -> None:
    w = await make_world(sm, await ledger_owners())
    async with sm() as s:
        await persist_turn(s, w.owner.id, record(w))
    url = f"/v1/chat/sessions/{w.session_id}/messages"

    def evidence(body: dict[str, Any]) -> dict[str, Any]:
        return body["items"][1]["segments"][0]["evidence"][0]  # type: ignore[no-any-return]

    live = evidence((await api.get(url, headers=w.owner.auth)).json())
    assert live["deleted"] is False and live["cited_text"] == "Alpha beta."
    assert live["document_title"] == "Master Agreement"

    async with sm() as s:
        await s.execute(
            text("UPDATE documents SET deleted_at = now() WHERE id = :d"), {"d": w.doc.document_id}
        )
        await s.commit()
    body = (await api.get(url, headers=w.owner.auth)).json()
    ev = evidence(body)
    assert ev["deleted"] is True and ev["cited_text"] is None and ev["document_title"] is None
    assert body["items"][1]["segments"][0]["text"] == "Alpha is beta."  # the answer text stays
