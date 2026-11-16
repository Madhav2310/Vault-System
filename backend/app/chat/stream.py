"""SSE transport for a turn: frames the turn graph's events and the final `done`.

The turn runs as its own task (strong reference kept by the caller), never inside the response generator, so a
client disconnect only closes the generator: the turn keeps running and persists. A heartbeat comment goes out
every `HEARTBEAT_S` of silence. No DB connection is held while waiting: each read opens and ends its own
transaction on the request session.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.conflicts import load_conflicts
from app.chat.events import Done, Error, QueueSink, RunStarted, UsageOut, current_sink, to_sse

HEARTBEAT_S = 15.0
POLL_S = 0.5
# lease (LEASE_SECONDS) plus margin: a retried POST that attaches to a running turn gives up after this
MAX_WAIT_S = 150.0
HEARTBEAT_FRAME = ": heartbeat\n\n"
SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"}

_DONE = text(
    "SELECT m.id, m.response_type, m.content, r.reason_codes, r.input_tokens, r.output_tokens, r.cost_usd, "
    "r.latency_ms, r.ttft_ms FROM rag_runs r JOIN chat_messages m "
    "ON m.id = r.assistant_message_id AND m.owner_id = r.owner_id WHERE r.id = :r AND r.owner_id = :o"
)


async def load_done(db: AsyncSession, owner_id: uuid.UUID, run_id: uuid.UUID) -> Done | None:
    """The `done` event of a finished run from its stored rows; None while it has no assistant message."""
    row = (await db.execute(_DONE, {"r": run_id, "o": owner_id})).mappings().first()
    conflicts = await load_conflicts(db, owner_id, [run_id]) if row is not None else {}
    await db.rollback()  # end the read transaction: no connection is held while the stream waits
    if row is None:
        return None
    return Done(
        run_id=str(run_id),
        message_id=str(row["id"]),
        response_type=row["response_type"] or "error",
        content=row["content"],
        reason_codes=list(row["reason_codes"] or []),
        usage=UsageOut(
            input_tokens=int(row["input_tokens"] or 0),
            output_tokens=int(row["output_tokens"] or 0),
            cost_usd=float(row["cost_usd"] or 0),
        ),
        latency_ms=row["latency_ms"],
        ttft_ms=row["ttft_ms"],
        conflicts=conflicts.get(run_id, []),
    )


def start_sink_task(
    start: Callable[[], Awaitable[None]], tasks: set[asyncio.Future[None]]
) -> tuple[QueueSink, asyncio.Future[None]]:
    """Run `start()` (the turn) as a task whose context carries a `QueueSink`; the task is kept in `tasks`."""
    sink = QueueSink()
    token = current_sink.set(sink)
    try:
        task: asyncio.Future[None] = asyncio.ensure_future(start())  # copies the context, sink included
    finally:
        current_sink.reset(token)
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    return sink, task


async def stream_new_turn(
    db: AsyncSession,
    owner_id: uuid.UUID,
    run_id: uuid.UUID,
    user_message_id: uuid.UUID,
    sink: QueueSink,
    task: asyncio.Future[None],
) -> AsyncIterator[str]:
    """Frames of a turn this request started: `run.started`, the graph's events, then `done` (or `error`)."""
    rid = str(run_id)
    yield to_sse(RunStarted(run_id=rid, message_id=str(user_message_id)))
    get: asyncio.Future[Any] | None = None
    try:
        while True:
            if get is None:
                get = asyncio.ensure_future(sink.queue.get())
            finished, _ = await asyncio.wait(
                {get, task}, timeout=HEARTBEAT_S, return_when=asyncio.FIRST_COMPLETED
            )
            if get in finished:
                yield to_sse(get.result())
                get = None
            elif task in finished:
                break
            else:
                yield HEARTBEAT_FRAME
        while not sink.queue.empty():
            yield to_sse(sink.queue.get_nowait())
    finally:
        if get is not None and not get.done():
            get.cancel()  # the turn task is NOT cancelled: it persists even if the client is gone
    failed = task.cancelled() or task.exception() is not None
    done = None if failed else await load_done(db, owner_id, run_id)
    if done is None:
        yield to_sse(Error(run_id=rid, code="internal_error", retryable=True))
    else:
        yield to_sse(done)


async def stream_existing_turn(
    db: AsyncSession, owner_id: uuid.UUID, run_id: uuid.UUID, user_message_id: uuid.UUID
) -> AsyncIterator[str]:
    """A retried `client_message_id`: no re-run. A finished turn streams its stored `done`; one still running
    elsewhere is polled until it finishes."""
    rid = str(run_id)
    yield to_sse(RunStarted(run_id=rid, message_id=str(user_message_id)))
    waited, since_beat = 0.0, 0.0
    while True:
        done = await load_done(db, owner_id, run_id)
        if done is not None:
            yield to_sse(done)
            return
        if waited >= MAX_WAIT_S:
            yield to_sse(Error(run_id=rid, code="turn_in_progress", retryable=True))
            return
        await asyncio.sleep(POLL_S)
        waited, since_beat = waited + POLL_S, since_beat + POLL_S
        if since_beat >= HEARTBEAT_S:
            since_beat = 0.0
            yield HEARTBEAT_FRAME
