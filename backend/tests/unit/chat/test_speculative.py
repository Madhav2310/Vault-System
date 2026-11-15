"""`speculative.cancel`: drops the cancelled task's outcome, never the caller's own cancellation."""

import asyncio

import pytest

from app.chat.speculative import cancel


async def _slow_to_stop(stopped: asyncio.Event) -> None:
    try:
        await asyncio.sleep(10)
    except asyncio.CancelledError:
        await asyncio.sleep(0.2)  # cleanup on cancel, like closing a DB session mid-query
        stopped.set()
        raise


async def test_cancel_drops_the_tasks_outcome() -> None:
    async def boom() -> None:
        raise ValueError("speculative failure")

    t = asyncio.create_task(boom())
    await asyncio.sleep(0)
    await cancel(t)  # neither the task's error nor its cancellation reaches the caller
    stopped = asyncio.Event()
    t2 = asyncio.create_task(_slow_to_stop(stopped))
    await asyncio.sleep(0)
    await cancel(t2)
    assert t2.cancelled() and stopped.is_set()


async def test_node_deadline_during_cancel_still_times_out() -> None:
    """The graph runs each node under `asyncio.timeout`: a deadline that fires while the node waits for a
    cancelled speculative task to stop must surface as TimeoutError, not be swallowed."""
    stopped = asyncio.Event()
    t = asyncio.create_task(_slow_to_stop(stopped))
    await asyncio.sleep(0)
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.05):
            await cancel(t)


async def test_caller_cancellation_propagates() -> None:
    stopped = asyncio.Event()
    inner = asyncio.create_task(_slow_to_stop(stopped))
    await asyncio.sleep(0)
    outer = asyncio.create_task(cancel(inner))
    await asyncio.sleep(0.05)
    outer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await outer
