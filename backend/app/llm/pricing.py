"""Per-model prices (USD per million tokens) and the cost formula.

Sources: the providers' published list prices for input/output and Sonnet/Opus cache reads. Cache writes are
1.25x input (5-minute TTL; claude-api skill, prompt-caching "Economics"). Haiku 5.5's cache-read rate and its
>100K-prompt tier ($0.50 / $2.50) come from the claude-api skill model notes; the tier's cache rates are not
published there, so cache tokens keep the base rate. OpenAI GPT-6 prices: the published price list
(cache reads from the pricing page; 30-minute cache writes 1.25x input; above 272K prompt tokens 2x input and
cache, 1.5x output).
text-embedding-3-small is OpenAI's list price [C].
Every entry can be overridden by `Settings.llm_prices` without a code change.
"""

from collections.abc import Mapping
from dataclasses import dataclass, fields, replace
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
    long_cache_read: Decimal | None = None
    long_cache_write: Decimal | None = None


_WRITE_5M = Decimal("1.25")
_WRITE_30M = Decimal("1.25")  # OpenAI GPT-5.6+ 30-minute cache write (12 h would be 2.5x)
_OA_LONG = 272_000  # OpenAI: above this prompt size the whole request costs 2x input/cached, 1.5x output


def _oa(inp: str, cached: str, out: str) -> Price:
    i, c, o = _d(inp), _d(cached), _d(out)
    w = i * _WRITE_30M
    return Price(i, o, c, w, _OA_LONG, i * 2, o * Decimal("1.5"), c * 2, w * 2)


DEFAULT_PRICES: dict[str, Price] = {
    "claude-sonnet-5-5": Price(_d("2.00"), _d("10.00"), _d("0.20"), _d("2.00") * _WRITE_5M),
    "claude-opus-5-5": Price(_d("4.00"), _d("20.00"), _d("0.20"), _d("4.00") * _WRITE_5M),
    "claude-haiku-5-5": Price(
        _d("0.10"), _d("0.50"), _d("0.01"), _d("0.10") * _WRITE_5M, 100_000, _d("0.50"), _d("2.50")
    ),
    # OpenAI GPT-6 line (developers.openai.com/api/docs/pricing, probed 2026-11-13)
    "gpt-6-luna": _oa("0.10", "0.01", "0.50"),
    "gpt-6.1-terra": _oa("1.00", "0.05", "5.00"),
    "gpt-6.1-sol": _oa("2.00", "0.10", "10.00"),
    "gpt-6-astra": _oa("10.00", "1.00", "50.00"),
    "text-embedding-3-small": Price(_d("0.02"), Decimal(0), Decimal(0), Decimal(0)),
}

_FIELDS = {f.name for f in fields(Price)}


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
        cr = p.long_cache_read if long and p.long_cache_read is not None else p.cache_read
        cw = p.long_cache_write if long and p.long_cache_write is not None else p.cache_write
        total = (
            usage.input_tokens * inp
            + usage.output_tokens * out
            + usage.cache_read_tokens * cr
            + usage.cache_write_tokens * cw
        ) / _MTOK
        return total.quantize(_QUANTUM)


def price_table_from_settings(settings: Settings) -> PriceTable:
    prices = dict(DEFAULT_PRICES)
    for model, given in settings.llm_prices.items():
        unknown = set(given) - _FIELDS
        if unknown:
            raise ValueError(f"LLM_PRICES[{model}] has unknown fields {sorted(unknown)}")
        conv: dict[str, object] = {k: (int(v) if k == "long_threshold" else _d(v)) for k, v in given.items()}
        base = prices.get(model)
        if base is None:
            missing = {"input", "output", "cache_read", "cache_write"} - set(conv)
            if missing:
                raise ValueError(f"LLM_PRICES[{model}] (new model) lacks {sorted(missing)}")
            prices[model] = Price(**conv)  # type: ignore[arg-type]
        else:
            prices[model] = replace(base, **conv)  # type: ignore[arg-type]
    return PriceTable(prices)
