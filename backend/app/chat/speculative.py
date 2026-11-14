"""Speculative work started while a slower step runs (latency only; never changes what a turn returns).

`retrieval`: hybrid retrieval of the raw question, started with the analysis call. `retrieve` reuses it when
the analysis leaves the query and the scope as they were, else it is cancelled and the node retrieves again.
Whatever is left running when the turn ends is cancelled by `persist` (`cancel_all`).
"""

import asyncio
import contextlib
from dataclasses import dataclass
from typing import Any

from app.retrieval.retrieve import RetrievalResult
from app.retrieval.scope import Scope

HIT, MISS = "hit", "miss"


@dataclass
class SpeculativeRetrieval:
    task: asyncio.Task[RetrievalResult]
    query: str  # the raw question, whitespace collapsed
    scope: Scope  # the scope it ran on


async def cancel(task: asyncio.Task[Any] | None) -> None:
    """Cancel a task and wait for it to finish; its result or error is dropped."""
    if task is None:
        return
    if not task.done():
        task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await task


async def cancel_all(state: Any) -> None:
    """Cancel every speculative task still attached to the turn state."""
    spec_r, state.spec_retrieval = state.spec_retrieval, None
    await cancel(spec_r.task if spec_r else None)
