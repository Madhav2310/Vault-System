from decimal import Decimal

import pytest

from app.core.settings import Settings
from app.llm.errors import UnknownModelPriceError
from app.llm.pricing import DEFAULT_PRICES, PriceTable, price_table_from_settings
from app.llm.types import Usage

U = Usage(input_tokens=1_000, output_tokens=500, cache_read_tokens=2_000, cache_write_tokens=4_000)


def _settings(**kw: object) -> Settings:
    return Settings(_env_file=None, database_url="postgresql+asyncpg://x/y", redis_url="redis://x", **kw)  # type: ignore[call-arg, arg-type]


def test_cost_formula() -> None:
    t = PriceTable(DEFAULT_PRICES)
    # Sonnet 5.5: $2 in, $10 out, $0.20 cache read, $2.50 cache write (1.25x input), per MTok (design §4)
    assert t.cost("claude-sonnet-5-5", U) == Decimal("0.017400")  # (2000 + 5000 + 400 + 10000) / 1e6
    # Opus 5.5: $4 / $20 / $0.20 / $5.00
    assert t.cost("claude-opus-5-5", U) == Decimal("0.034400")  # (4000 + 10000 + 400 + 20000) / 1e6
    # Haiku 5.5 base tier: $0.10 / $0.50 / $0.01 / $0.125
    assert t.cost("claude-haiku-5-5", U) == Decimal("0.000870")  # (100 + 250 + 20 + 500) / 1e6
    # Haiku 5.5 above 100K prompt tokens (uncached + cache read + write): $0.50 / $2.50, cache rates unchanged
    big = Usage(input_tokens=100_000, output_tokens=1_000, cache_read_tokens=1)
    assert t.cost("claude-haiku-5-5", big) == Decimal("0.052500")  # (50000 + 2500 + 0.01) / 1e6 → rounds
    at_threshold = Usage(input_tokens=100_000)
    assert t.cost("claude-haiku-5-5", at_threshold) == Decimal("0.010000")
    # embeddings: input only
    assert t.cost("text-embedding-3-small", Usage(input_tokens=1_000_000)) == Decimal("0.020000")
    assert t.cost("claude-sonnet-5-5", Usage()) == Decimal("0.000000")

    with pytest.raises(UnknownModelPriceError):
        t.cost("claude-sonnet-5-5-20260101", U)

    # settings override merges over the defaults and can add a model
    s = _settings(
        llm_prices={
            "claude-sonnet-5-5": {"input": 3},
            "new-model": {"input": 1, "output": 2, "cache_read": 0, "cache_write": 0},
        }
    )
    t2 = price_table_from_settings(s)
    assert t2.cost("claude-sonnet-5-5", Usage(input_tokens=1_000_000)) == Decimal("3.000000")
    assert t2.cost("claude-sonnet-5-5", Usage(output_tokens=1_000_000)) == Decimal("10.000000")
    assert t2.cost("new-model", Usage(output_tokens=1_000_000)) == Decimal("2.000000")
    with pytest.raises(ValueError, match="unknown fields"):
        price_table_from_settings(_settings(llm_prices={"claude-sonnet-5-5": {"inptu": 3}}))
    with pytest.raises(ValueError, match="lacks"):
        price_table_from_settings(_settings(llm_prices={"other": {"input": 3}}))
