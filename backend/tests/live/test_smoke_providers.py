"""Live provider smoke: one `fast`-role strict-schema call (gpt-6-luna)
and one embedding call through the gateway.

Spends real money (well under $0.001 each). Run with `make smoke-providers`; excluded from `make ci`.
Fails (does not skip) when keys are missing, so the make target cannot pass vacuously.
"""

import os
from decimal import Decimal

import pytest
from pydantic import BaseModel, ConfigDict

from app.core.settings import get_settings
from app.llm.gateway import LlmGateway, MemoryLedger
from app.llm.pricing import price_table_from_settings
from app.llm.prompts import default_registry
from app.llm.providers.openai import OpenAIEmbeddingProvider
from app.llm.providers.openai_chat import OpenAIResponsesProvider

pytestmark = pytest.mark.live


class Echo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    echo: str


def _gateway(ledger: MemoryLedger, need: tuple[str, ...]) -> LlmGateway:
    s = get_settings()
    missing = [k for k in need if not (os.environ.get(k) or getattr(s, k.lower()))]
    if missing:
        pytest.fail(f"live smoke needs {missing} in the environment or .env")
    o = s.openai_api_key.get_secret_value() if s.openai_api_key else None
    return LlmGateway(
        chat=OpenAIResponsesProvider(o, s.llm_timeout_s),
        embeddings=OpenAIEmbeddingProvider(o, s.llm_timeout_s),
        prompts=default_registry(),
        prices=price_table_from_settings(s),
        ledger=ledger,
        settings=s,
    )


def _report(ledger: MemoryLedger) -> None:
    for rec in ledger.records:  # token counts are the AC13 evidence; no content is printed
        u = rec.usage
        print(
            f"\n{rec.provider} {rec.model} attempt={rec.attempt} status={rec.status} in={u.input_tokens} "
            f"out={u.output_tokens} cache_r={u.cache_read_tokens} cache_w={u.cache_write_tokens} "
            f"cost=${rec.cost_usd} latency={rec.latency_ms}ms"
        )


async def test_live_fast_structured() -> None:
    ledger = MemoryLedger()
    gw = _gateway(ledger, ("OPENAI_API_KEY",))
    r = await gw.call(
        purpose="smoke",
        lane="background",
        prompt=("_example", 1),
        owner_id=None,
        schema=Echo,
        inputs={"word": "vault"},
    )
    assert r.output is not None and r.output.echo == "vault"
    _report(ledger)
    assert [rec.provider for rec in ledger.records if rec.status == "ok"] == ["openai"]
    assert [(rec.role, rec.model) for rec in ledger.records] == [("fast", "gpt-6-luna")]  # routed, not pinned
    assert sum(rec.cost_usd for rec in ledger.records) < Decimal("0.005")


async def test_live_embedding() -> None:
    ledger = MemoryLedger()
    gw = _gateway(ledger, ("OPENAI_API_KEY",))
    e = await gw.embed(purpose="smoke", lane="background", texts=["evidence-first"], owner_id=None)
    assert len(e.vectors[0]) == get_settings().embedding_dim
    _report(ledger)
    assert [rec.provider for rec in ledger.records if rec.status == "ok"] == ["openai"]
