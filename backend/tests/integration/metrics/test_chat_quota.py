"""S18.08 `quota` block of GET /v1/metrics/chat."""

from collections.abc import Callable
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy import text

from app.core.settings import get_settings


async def _spend(sm: Any, owner_id: Any, usd: str) -> None:
    async with sm() as s:
        await s.execute(
            text(
                "INSERT INTO llm_calls (id, owner_id, purpose, lane, provider, model, attempt, cost_usd, "
                "status) VALUES (gen_random_uuid(), :o, 'chat_generate', 'interactive', 'openai', 'm', 1, "
                ":c, 'ok')"
            ),
            {"o": owner_id, "c": Decimal(usd)},
        )
        await s.commit()


async def test_values(api: httpx.AsyncClient, sm: Any, mk_owner: Callable[..., Any]) -> None:
    a, b, admin = await mk_owner(), await mk_owner(), await mk_owner(admin=True)
    for o, usd in ((a, "0.25"), (a, "0.125"), (b, "9"), (admin, "3")):
        await _spend(sm, o.id, usd)
    q = (await api.get("/v1/metrics/chat", headers=a.auth)).json()["quota"]
    async with sm() as s:  # independent of the code under test: a plain SUM over the ledger
        ledger = (
            await s.execute(
                text(
                    "SELECT sum(cost_usd) FROM llm_calls WHERE owner_id = :o "
                    "AND created_at >= date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'"
                ),
                {"o": a.id},
            )
        ).scalar_one()
    assert float(ledger) == 0.375
    assert q == {"limit_usd": get_settings().user_daily_usd, "spent_today_usd": float(ledger)}
    adm = (await api.get("/v1/metrics/chat", headers=admin.auth)).json()["quota"]
    assert adm == {"limit_usd": None, "spent_today_usd": 3.0}
