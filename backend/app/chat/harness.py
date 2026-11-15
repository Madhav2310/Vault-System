"""Turn limits and budget. `TurnBudget` satisfies the gateway's `Budget` protocol."""

import time
from dataclasses import dataclass, field

from app.llm.errors import BudgetExceededError
from app.llm.types import Usage


@dataclass(frozen=True)
class TurnLimits:
    max_llm_calls: int
    deadline_s: float
    max_evidence_tokens: int


ROUTE_LIMITS: dict[str, TurnLimits] = {
    "qa": TurnLimits(4, 30.0, 6000),
    "compare": TurnLimits(4, 45.0, 10000),
    "summarize": TurnLimits(2, 60.0, 0),
    "meta": TurnLimits(1, 10.0, 0),
    "chitchat": TurnLimits(1, 10.0, 0),
}
DEFAULT_LIMITS = TurnLimits(4, 30.0, 6000)


@dataclass
class TurnBudget:
    limits: TurnLimits
    deadline_at: float = field(default=0.0)
    calls_used: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    def __post_init__(self) -> None:
        if not self.deadline_at:
            self.deadline_at = time.monotonic() + self.limits.deadline_s

    def charge_call(self) -> None:
        if self.calls_used >= self.limits.max_llm_calls:
            raise BudgetExceededError("turn call budget exhausted")
        self.calls_used += 1

    def record_usage(self, usage: Usage) -> None:
        self.input_tokens += usage.input_tokens + usage.cache_read_tokens
        self.output_tokens += usage.output_tokens

    def remaining_s(self) -> float:
        return max(0.0, self.deadline_at - time.monotonic())
