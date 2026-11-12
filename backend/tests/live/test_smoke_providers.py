"""Live provider smoke (S0.13 AC13): one Haiku structured call and one embedding call through the gateway.
Spends real money (well under $0.001). Run with `make smoke-providers`; excluded from `make ci`.
Fails (does not skip) when keys are missing, so the make target cannot pass vacuously."""

import os

import pytest
from pydantic import BaseModel, ConfigDict

from app.core.settings import get_settings
from app.llm.gateway import LlmGateway, MemoryLedger
from app.llm.pricing import price_table_from_settings
from app.llm.prompts import default_registry
from app.llm.providers.anthropic import AnthropicProvider
from app.llm.providers.openai import OpenAIEmbeddingProvider

pytestmark = pytest.mark.live


class Echo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    echo: str


def _gateway(ledger: MemoryLedger) -> LlmGateway:
    s = get_settings()
    missing = [
        k for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY") if not (os.environ.get(k) or getattr(s, k.lower()))
    ]
    if missing:
        pytest.fail(f"live smoke needs {missing} in the environment or .env")
    a = s.anthropic_api_key.get_secret_value() if s.anthropic_api_key else None
    o = s.openai_api_key.get_secret_value() if s.openai_api_key else None
    return LlmGateway(
        chat=AnthropicProvider(a, s.llm_timeout_s),
        embeddings=OpenAIEmbeddingProvider(o, s.llm_timeout_s),
        prompts=default_registry(),
        prices=price_table_from_settings(s),
        ledger=ledger,
        settings=s,
    )


async def test_live_haiku_and_embedding() -> None:
    ledger = MemoryLedger()
    gw = _gateway(ledger)

    r = await gw.call(
        purpose="smoke",
        lane="background",
        prompt=("_example", 1),
        owner_id=None,
        schema=Echo,
        inputs={"word": "vault"},
    )
    assert r.output is not None and r.output.echo == "vault"
    e = await gw.embed(purpose="smoke", lane="background", texts=["evidence-first"], owner_id=None)
    assert len(e.vectors[0]) == get_settings().embedding_dim

    for rec in ledger.records:  # token counts are the AC13 evidence; no content is printed
        u = rec.usage
        print(
            f"\n{rec.provider} {rec.model} attempt={rec.attempt} status={rec.status} in={u.input_tokens} "
            f"out={u.output_tokens} cache_r={u.cache_read_tokens} cache_w={u.cache_write_tokens} "
            f"cost=${rec.cost_usd} latency={rec.latency_ms}ms"
        )
    assert {rec.provider for rec in ledger.records if rec.status == "ok"} == {"anthropic", "openai"}
