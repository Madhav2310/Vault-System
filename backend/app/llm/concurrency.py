"""Cap on model calls in flight, one pool of slots per lane, owned by one gateway.

A gateway is one per process in the API (`get_gateway`) and one per task in a prefork Celery child, which runs
one task at a time, so the cap is per process. Lanes have separate pools, so background work (ingestion,
briefs) never takes a slot chat needs. A waiter gives up at its deadline less `margin_s`, or after
`max_wait_s`, whichever comes first, with `CapacityError`; a free slot is always taken, whatever the deadline.
Waiters are served in arrival order (`asyncio.Semaphore`).
"""

import asyncio
import time
from collections.abc import Mapping

from opentelemetry import metrics

from app.core.settings import Settings
from app.llm.errors import CapacityError
from app.llm.types import Lane

_meter = metrics.get_meter("vault.llm")
_rejected = _meter.create_counter(
    "vault.llm.capacity_rejected", description="Model calls refused because no lane slot came free in time"
)
_queue_wait = _meter.create_histogram(
    "vault.llm.queue_wait", unit="s", description="Time a model call waited for a lane slot"
)


class ConcurrencyLimiter:
    def __init__(self, limits: Mapping[Lane, int], *, max_wait_s: float, margin_s: float) -> None:
        self._sems = {lane: asyncio.Semaphore(max(1, n)) for lane, n in limits.items()}
        self._busy = dict.fromkeys(limits, 0)
        self.max_wait_s, self.margin_s = max_wait_s, margin_s

    @classmethod
    def from_settings(cls, s: Settings) -> "ConcurrencyLimiter":
        return cls(
            {"interactive": s.llm_concurrency_interactive, "background": s.llm_concurrency_background},
            max_wait_s=s.llm_queue_max_wait_s,
            margin_s=s.llm_queue_deadline_margin_s,
        )

    def in_flight(self, lane: Lane) -> int:
        return self._busy[lane]

    async def acquire(self, lane: Lane, deadline_at: float | None) -> float:
        """Take a slot of `lane`; `deadline_at` is on the `time.monotonic()` clock. Returns the wait in s."""
        sem, t0 = self._sems[lane], time.monotonic()
        if sem.locked():
            wait = self.max_wait_s
            if deadline_at is not None:
                wait = min(wait, deadline_at - t0 - self.margin_s)
            try:
                async with asyncio.timeout(max(0.0, wait)):
                    await sem.acquire()
            except TimeoutError:
                _rejected.add(1, {"vault.llm.lane": lane})
                raise CapacityError(f"{lane} lane at capacity") from None
        else:
            await sem.acquire()  # free: returns without suspending
        self._busy[lane] += 1
        waited = time.monotonic() - t0
        _queue_wait.record(waited, {"vault.llm.lane": lane})
        return waited

    def release(self, lane: Lane) -> None:
        self._busy[lane] -= 1
        self._sems[lane].release()
