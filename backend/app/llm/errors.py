"""Typed LLM errors. Providers translate SDK exceptions into these; nothing above
`app.llm` ever sees an SDK exception. `error_type` is a bounded enum value: it goes into `llm_calls`
and metric labels. Messages never carry prompt or completion text."""

import email.utils
import time
import uuid
from collections.abc import Mapping
from decimal import Decimal
from typing import TYPE_CHECKING, ClassVar, Self

if TYPE_CHECKING:
    from app.llm.types import Usage


def parse_retry_after(headers: Mapping[str, str] | None) -> float | None:
    """`retry-after` as seconds (delta-seconds or HTTP-date form); None when absent or unparseable."""
    raw = headers.get("retry-after") if headers is not None else None
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        try:
            return max(0.0, email.utils.parsedate_to_datetime(raw).timestamp() - time.time())
        except (TypeError, ValueError):
            return None


class LlmError(Exception):
    error_type: ClassVar[str] = "llm_error"
    retryable: ClassVar[bool] = False
    # Outcome errors (refusal, truncation) are raised after the ledger row is written: the gateway attaches
    # that row's accounting so callers keep usage, cost and call ids.
    usage: "Usage | None" = None
    cost_usd: Decimal = Decimal(0)
    call_ids: tuple[uuid.UUID, ...] = ()

    def with_accounting(self, usage: "Usage", cost_usd: Decimal, call_ids: tuple[uuid.UUID, ...]) -> Self:
        self.usage, self.cost_usd, self.call_ids = usage, cost_usd, call_ids
        return self


# --- provider transport/status errors -------------------------------------------------------------


class ProviderError(LlmError):
    error_type = "provider_error"

    def __init__(
        self, message: str = "", *, status_code: int | None = None, retry_after: float | None = None
    ):
        super().__init__(message or self.error_type)
        self.status_code = status_code
        self.retry_after = retry_after


class RateLimitedError(ProviderError):
    error_type = "rate_limited"
    retryable = True


class OverloadedError(ProviderError):
    error_type = "overloaded"
    retryable = True


class ProviderTimeoutError(ProviderError):
    error_type = "timeout"
    retryable = True


class ProviderConnectionError(ProviderError):
    error_type = "connection"
    retryable = True


class ProviderServerError(ProviderError):
    error_type = "server_error"
    retryable = True


class BadRequestError(ProviderError):
    error_type = "bad_request"


class ProviderAuthError(ProviderError):
    error_type = "auth"


# --- outcome errors (the call completed, but the result is unusable) -------------------------------


class RefusalError(LlmError):
    error_type = "refusal"

    def __init__(self, category: str | None = None):
        super().__init__(f"refusal (category={category})")
        self.category = category


class TruncatedError(LlmError):
    error_type = "max_tokens"


class SchemaValidationError(LlmError):
    error_type = "schema_invalid"


# --- gateway-side refusals to call -----------------------------------------------------------------


class RequestConflictError(LlmError):
    """The request is invalid before it reaches a provider (e.g. citations + `output_config.format`)."""

    error_type = "invalid_request"


class UnknownModelPriceError(LlmError):
    error_type = "unknown_price"


class CapacityError(LlmError):
    """No call slot of the lane came free within the caller's deadline or the max queue wait. Raised before
    any provider call; `error_type` is the API's `capacity` code (503, retryable)."""

    error_type = "capacity"
    retryable = True


class BudgetExceededError(LlmError):
    error_type = "budget_exceeded"
