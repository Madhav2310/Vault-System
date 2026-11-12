"""Per-model prices (USD per million tokens) and the cost formula.

Sources: design §4 [V, skill cache 2026-10-06] for input/output and Sonnet/Opus cache reads. Cache writes are
1.25x input (5-minute TTL; claude-api skill, prompt-caching "Economics"). Haiku 5.5's cache-read rate and its
>100K-prompt tier ($0.50 / $2.50) come from the claude-api skill model notes; the tier's cache rates are not
published there, so cache tokens keep the base rate. text-embedding-3-small is OpenAI's list price [C].
Every entry can be overridden by `Settings.llm_prices` without a code change.
"""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from decimal import Decimal

from app.core.settings import Settings
from app.llm.errors import UnknownModelPriceError
from app.llm.types import Usage

_MTOK = Decimal(1_000_000)
_QUANTUM = Decimal("0.000001")  # llm_calls.cost_usd is numeric(10,6)


def _d(x: float | str) -> Decimal:
    return Decimal(str(x))


@dataclass(frozen=True)
class Price:
    input: Decimal
    output: Decimal
    cache_read: Decimal
    cache_write: Decimal
    # Long-prompt tier: applies when the prompt (uncached + cache read + cache write) exceeds the threshold.
    long_threshold: int | None = None
    long_input: Decimal | None = None
    long_output: Decimal | None = None


_WRITE_5M = Decimal("1.25")

DEFAULT_PRICES: dict[str, Price] = {
    "claude-sonnet-5-5": Price(_d("2.00"), _d("10.00"), _d("0.20"), _d("2.00") * _WRITE_5M),
    "claude-opus-5-5": Price(_d("4.00"), _d("20.00"), _d("0.20"), _d("4.00") * _WRITE_5M),
    "claude-haiku-5-5": Price(
        _d("0.10"), _d("0.50"), _d("0.01"), _d("0.10") * _WRITE_5M, 100_000, _d("0.50"), _d("2.50")
    ),
    "text-embedding-3-small": Price(_d("0.02"), Decimal(0), Decimal(0), Decimal(0)),
}

_FIELDS = {"input", "output", "cache_read", "cache_write", "long_threshold", "long_input", "long_output"}


class PriceTable:
    def __init__(self, prices: Mapping[str, Price]):
        self._prices = dict(prices)

    def price(self, model: str) -> Price:
        try:
            return self._prices[model]
        except KeyError:
            raise UnknownModelPriceError(f"no price for model {model!r}") from None

    def cost(self, model: str, usage: Usage) -> Decimal:
        p = self.price(model)
        prompt = usage.input_tokens + usage.cache_read_tokens + usage.cache_write_tokens
        long = p.long_threshold is not None and prompt > p.long_threshold
        inp = p.long_input if long and p.long_input is not None else p.input
        out = p.long_output if long and p.long_output is not None else p.output
        total = (
            usage.input_tokens * inp
            + usage.output_tokens * out
            + usage.cache_read_tokens * p.cache_read
            + usage.cache_write_tokens * p.cache_write
        ) / _MTOK
        return total.quantize(_QUANTUM)


def price_table_from_settings(settings: Settings) -> PriceTable:
    prices = dict(DEFAULT_PRICES)
    for model, fields in settings.llm_prices.items():
        unknown = set(fields) - _FIELDS
        if unknown:
            raise ValueError(f"LLM_PRICES[{model}] has unknown fields {sorted(unknown)}")
        conv: dict[str, object] = {k: (int(v) if k == "long_threshold" else _d(v)) for k, v in fields.items()}
        base = prices.get(model)
        if base is None:
            missing = {"input", "output", "cache_read", "cache_write"} - set(conv)
            if missing:
                raise ValueError(f"LLM_PRICES[{model}] (new model) lacks {sorted(missing)}")
            prices[model] = Price(**conv)  # type: ignore[arg-type]
        else:
            prices[model] = replace(base, **conv)  # type: ignore[arg-type]
    return PriceTable(prices)
