"""Test helpers: a gateway over FakeProvider with a small prompt registry. No network."""

import asyncio
import pathlib
import time
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict

from app.core.settings import Settings
from app.llm.errors import BudgetExceededError
from app.llm.gateway import LedgerWriter, LlmGateway, MemoryLedger
from app.llm.pricing import DEFAULT_PRICES, PriceTable
from app.llm.prompts import PromptRegistry
from app.llm.providers.fake import FakeProvider, Step
from app.llm.types import Usage

PROMPTS = {
    "t_schema@1.md": ("t_schema", 1, "schema", "low"),
    "t_cite@1.md": ("t_cite", 1, "citations", "high"),
    "t_text@1.md": ("t_text", 1, "text", "medium"),
}


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: str
    confidence: float


def write_prompt(d: pathlib.Path, name: str, **fields: object) -> pathlib.Path:
    head = "\n".join(f"{k}: {v}" for k, v in fields.items())
    path = d / name
    path.write_text(f"---\n{head}\n---\nSystem prompt for {name}.\n")
    return path


def make_registry(d: pathlib.Path) -> PromptRegistry:
    for name, (pid, ver, output, effort) in PROMPTS.items():
        write_prompt(
            d,
            name,
            id=pid,
            version=ver,
            purpose=pid,
            role="fast",
            output=output,
            effort=effort,
            max_tokens=512,
        )
    return PromptRegistry.load(d)


@dataclass
class SleepRecorder:
    """Records requested delays; sleeps for real only when `real=True`."""

    delays: list[float] = field(default_factory=list)
    real: bool = False

    async def __call__(self, s: float) -> None:
        self.delays.append(s)
        if self.real:
            await asyncio.sleep(s)


def make_gateway(
    tmp: pathlib.Path,
    script: list[Step] | None = None,
    *,
    ledger: LedgerWriter | None = None,
    sleep: SleepRecorder | None = None,
) -> tuple[LlmGateway, FakeProvider, MemoryLedger | LedgerWriter, SleepRecorder]:
    fake = FakeProvider(script=script or [])
    led = ledger or MemoryLedger()
    slp = sleep or SleepRecorder()
    settings = Settings(_env_file=None, database_url="postgresql+asyncpg://x/y", redis_url="redis://x")  # type: ignore[call-arg]
    gw = LlmGateway(
        chat=fake,
        embeddings=fake,
        prompts=make_registry(tmp),
        prices=PriceTable({**DEFAULT_PRICES}),
        ledger=led,
        settings=settings,
        sleep=slp,
        rng=lambda: 0.5,
    )
    return gw, fake, led, slp


@dataclass
class FakeBudget:
    max_calls: int = 10
    deadline_s: float | None = None
    calls: int = 0
    usage: list[Usage] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._deadline = time.monotonic() + self.deadline_s if self.deadline_s is not None else None

    @property
    def deadline_at(self) -> float | None:
        return self._deadline

    def charge_call(self) -> None:
        if self.calls >= self.max_calls:
            raise BudgetExceededError("max_llm_calls")
        self.calls += 1

    def record_usage(self, usage: Usage) -> None:
        self.usage.append(usage)
