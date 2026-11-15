"""Per-lane concurrency cap on model calls, and the estimated cost of attempts that end without usage."""

import asyncio
import pathlib
import time
import uuid
from contextlib import aclosing
from typing import Any

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.core.error_codes import ErrorCode
from app.ingestion.chunk import count_tokens
from app.llm import errors
from app.llm import gateway as gateway_mod
from app.llm.concurrency import ConcurrencyLimiter
from app.llm.gateway import LlmGateway, MemoryLedger
from app.llm.providers import fake
from app.llm.providers.fake import FakeProvider
from app.llm.types import ChatRequest, ChatResponse, TextDelta, Usage
from tests.support.llm import FakeBudget, make_gateway

OWNER = uuid.UUID(int=1)
BIG = {"q": "x" * 4000}  # about 1,000 estimated input tokens, so an estimate is visibly above $0


def _limit(
    gw: LlmGateway,
    *,
    interactive: int = 1,
    background: int = 1,
    max_wait_s: float = 5.0,
    margin_s: float = 0.0,
) -> ConcurrencyLimiter:
    gw.limiter = ConcurrencyLimiter(
        {"interactive": interactive, "background": background}, max_wait_s=max_wait_s, margin_s=margin_s
    )
    return gw.limiter


def _call(gw: LlmGateway, lane: Any = "interactive", inputs: dict[str, str] | None = None, **kw: Any) -> Any:
    return gw.call(
        purpose="t", lane=lane, prompt=("t_text", 1), owner_id=OWNER, inputs=inputs or {"q": "x"}, **kw
    )


def _stream(gw: LlmGateway, inputs: dict[str, str] | None = None, **kw: Any) -> Any:
    return gw.stream(
        purpose="t", lane="interactive", prompt=("t_text", 1), owner_id=OWNER, inputs=inputs or BIG, **kw
    )


def _track(prov: FakeProvider) -> dict[str, int]:
    """Counts provider calls in flight (and the peak) around `complete`."""
    state = {"now": 0, "peak": 0}
    real = prov.complete

    async def complete(req: ChatRequest) -> ChatResponse:
        state["now"] += 1
        state["peak"] = max(state["peak"], state["now"])
        try:
            return await real(req)
        finally:
            state["now"] -= 1

    prov.complete = complete  # type: ignore[method-assign]
    return state


async def _until(cond: Any) -> None:
    for _ in range(200):
        if cond():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("condition not reached")


def _est_input(req: ChatRequest) -> int:
    """The gateway's input-token estimate: characters of the system prompt and messages / 4."""
    return (len(req.system) + sum(len(str(m.get("content", ""))) for m in req.messages)) // 4


# --- the cap -----------------------------------------------------------------------------------------


async def test_in_flight_calls_never_exceed_the_lane_cap(tmp_path: pathlib.Path) -> None:
    gw, prov, _, _ = make_gateway(tmp_path)
    lim = _limit(gw, interactive=3)
    prov.latency_s = 0.02
    state = _track(prov)
    results = await asyncio.gather(*(_call(gw) for _ in range(10)))
    assert len(results) == 10 and len(prov.requests) == 10
    assert state["peak"] == 3
    assert lim.in_flight("interactive") == 0


async def test_a_queued_call_past_its_deadline_gets_capacity_without_a_provider_call(
    tmp_path: pathlib.Path,
) -> None:
    gw, prov, led, _ = make_gateway(tmp_path)
    lim = _limit(gw, margin_s=0.1)
    prov.latency_s = 0.6
    holder = asyncio.create_task(_call(gw))
    await _until(lambda: lim.in_flight("interactive") == 1)

    t0 = time.monotonic()
    with pytest.raises(errors.CapacityError) as exc:
        await _call(gw, budget=FakeBudget(deadline_s=0.3))
    waited = time.monotonic() - t0
    assert 0.15 <= waited < 0.45  # the remaining time minus the margin, not the holder's full call
    assert exc.value.retryable and ErrorCode(exc.value.error_type) is ErrorCode.CAPACITY
    assert ErrorCode.CAPACITY.spec.status == 503
    assert len(prov.requests) == 1  # only the holder reached the provider
    assert isinstance(led, MemoryLedger) and led.records == []  # and no row for the call that never ran

    await holder
    assert [r.status for r in led.records] == ["ok"]


async def test_a_call_without_deadline_waits_at_most_the_max_queue_wait(tmp_path: pathlib.Path) -> None:
    gw, prov, led, _ = make_gateway(tmp_path)
    lim = _limit(gw, max_wait_s=0.1)
    prov.latency_s = 0.5
    holder = asyncio.create_task(gw.embed(purpose="e", lane="background", texts=["a"], owner_id=OWNER))
    await _until(lambda: lim.in_flight("background") == 1)
    with pytest.raises(errors.CapacityError):
        await gw.embed(purpose="e", lane="background", texts=["b"], owner_id=OWNER)
    assert len(prov.embed_calls) == 1
    await holder
    assert isinstance(led, MemoryLedger) and len(led.records) == 1


async def test_background_work_cannot_take_interactive_slots(tmp_path: pathlib.Path) -> None:
    gw, prov, _, _ = make_gateway(tmp_path)
    lim = _limit(gw, interactive=1, background=1)
    prov.latency_s = 0.3
    held = asyncio.create_task(gw.embed(purpose="e", lane="background", texts=["a"], owner_id=OWNER))
    await _until(lambda: lim.in_flight("background") == 1)
    prov.latency_s = 0.0
    r = await asyncio.wait_for(_call(gw), 0.2)  # chat is not queued behind the background call
    assert r.text == "ok" and not held.done()
    await held


async def test_a_retry_gives_its_slot_back_during_the_backoff(tmp_path: pathlib.Path) -> None:
    gw, prov, _, _ = make_gateway(tmp_path, [fake.rate_limited(0.0), fake.overloaded()])
    lim = _limit(gw)
    seen: list[int] = []

    async def sleep(_: float) -> None:
        seen.append(lim.in_flight("interactive"))

    gw._sleep = sleep
    await _call(gw)
    async for _ in _stream(gw):
        pass
    assert seen == [0, 0] and len(prov.requests) == 4
    assert lim.in_flight("interactive") == 0


async def test_queue_wait_is_on_the_attempt_span(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(gateway_mod, "_tracer", provider.get_tracer("t"))
    gw, prov, _, _ = make_gateway(tmp_path)
    _limit(gw)
    prov.latency_s = 0.15
    await asyncio.gather(_call(gw), _call(gw))
    waits = sorted(int(s.attributes["vault.llm.queue_wait_ms"]) for s in exporter.get_finished_spans())  # type: ignore[index, arg-type]
    assert waits[0] < 50 and waits[1] >= 100


# --- slots are released however the attempt ends --------------------------------------------------


async def test_cancelling_a_stream_consumer_releases_the_slot_and_records_an_estimate(
    tmp_path: pathlib.Path,
) -> None:
    gw, prov, led, _ = make_gateway(tmp_path, [fake.text("alpha beta gamma delta epsilon zeta")])
    lim = _limit(gw)
    got: list[TextDelta] = []
    two = asyncio.Event()

    async def consume() -> None:  # the shape of the `generate` node: aclosing around the stream
        async with aclosing(_stream(gw)) as events:
            async for ev in events:
                assert isinstance(ev, TextDelta)
                got.append(ev)
                if len(got) == 2:
                    two.set()
                    await asyncio.sleep(10)  # a slow consumer, cut by the node deadline

    task = asyncio.create_task(consume())
    await two.wait()
    assert lim.in_flight("interactive") == 1
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert lim.in_flight("interactive") == 0

    assert isinstance(led, MemoryLedger)
    (row,) = led.records
    expected = Usage(input_tokens=_est_input(prov.requests[0]), output_tokens=count_tokens("alpha beta "))
    assert (row.status, row.error_type, row.usage) == ("estimated", "cancelled", expected)
    assert row.cost_usd == gw.prices.cost(row.model, expected) and row.cost_usd > 0
    await asyncio.wait_for(_call(gw), 1)  # the slot is usable again


async def test_cancelling_a_stream_before_its_first_token_records_the_input_estimate(
    tmp_path: pathlib.Path,
) -> None:
    gw, prov, led, _ = make_gateway(tmp_path)
    lim = _limit(gw)
    prov.latency_s = 5  # cancelled inside the gateway, while it awaits the provider

    async def consume() -> None:
        async for _ in _stream(gw):
            pass

    task = asyncio.create_task(consume())
    await _until(lambda: len(prov.requests) == 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert lim.in_flight("interactive") == 0
    assert isinstance(led, MemoryLedger)
    (row,) = led.records
    assert (row.status, row.error_type, row.ttft_ms) == ("estimated", "cancelled", None)
    assert row.usage == Usage(input_tokens=_est_input(prov.requests[0]))


async def test_closing_a_stream_early_releases_the_slot(tmp_path: pathlib.Path) -> None:
    gw, _, led, _ = make_gateway(tmp_path, [fake.text("one two three")])
    lim = _limit(gw)
    async with aclosing(_stream(gw)) as events:
        async for _ in events:
            break
    assert lim.in_flight("interactive") == 0
    assert isinstance(led, MemoryLedger)
    assert [(r.status, r.error_type, r.usage.output_tokens) for r in led.records] == [
        ("estimated", "cancelled", count_tokens("one "))
    ]


async def test_a_provider_error_mid_stream_releases_the_slot_and_estimates_what_was_streamed(
    tmp_path: pathlib.Path,
) -> None:
    broken = fake.text("a b c d", fail_after_events=2, fail_with=fake.timeout())
    gw, prov, led, _ = make_gateway(tmp_path, [broken])
    lim = _limit(gw)
    with pytest.raises(errors.ProviderTimeoutError):
        async for _ in _stream(gw):
            pass
    assert lim.in_flight("interactive") == 0
    assert isinstance(led, MemoryLedger)
    (row,) = led.records
    expected = Usage(input_tokens=_est_input(prov.requests[0]), output_tokens=count_tokens("a b "))
    assert (row.status, row.error_type, row.usage) == ("estimated", "timeout", expected)
    assert row.cost_usd > 0


async def test_a_provider_error_before_the_first_token_stays_at_zero(tmp_path: pathlib.Path) -> None:
    gw, _, led, _ = make_gateway(tmp_path, [fake.bad_request()])
    lim = _limit(gw)
    with pytest.raises(errors.BadRequestError):
        async for _ in _stream(gw):
            pass
    assert lim.in_flight("interactive") == 0
    assert isinstance(led, MemoryLedger)
    assert [(r.status, r.cost_usd) for r in led.records] == [("error", 0)]


async def test_cancelling_a_request_response_call_records_the_input_estimate(tmp_path: pathlib.Path) -> None:
    gw, prov, led, _ = make_gateway(tmp_path)
    lim = _limit(gw)
    prov.latency_s = 5
    task = asyncio.create_task(_call(gw, inputs=BIG))
    await _until(lambda: len(prov.requests) == 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert lim.in_flight("interactive") == 0
    assert isinstance(led, MemoryLedger)
    (row,) = led.records
    expected = Usage(input_tokens=_est_input(prov.requests[0]))
    assert (row.status, row.error_type, row.usage) == ("estimated", "cancelled", expected)
    assert row.cost_usd == gw.prices.cost(row.model, expected) and row.cost_usd > 0


async def test_a_call_cancelled_while_queued_records_nothing(tmp_path: pathlib.Path) -> None:
    gw, prov, led, _ = make_gateway(tmp_path)
    lim = _limit(gw)
    prov.latency_s = 0.2
    holder = asyncio.create_task(_call(gw))
    await _until(lambda: lim.in_flight("interactive") == 1)
    queued = asyncio.create_task(_call(gw))
    await asyncio.sleep(0.02)
    queued.cancel()
    with pytest.raises(asyncio.CancelledError):
        await queued
    await holder
    assert len(prov.requests) == 1
    assert isinstance(led, MemoryLedger) and [r.status for r in led.records] == ["ok"]
    assert lim.in_flight("interactive") == 0
