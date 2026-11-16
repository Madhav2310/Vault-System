"""Offline: `eval.run --stream` reads the SSE stream, measures TTFT and reports p50/p95."""

import json
import uuid
from collections.abc import AsyncIterator

import pytest
from eval import run as eval_run
from eval.report import ItemResult, RunResult
from eval.schema import ItemOutput
from eval.sse import read_turn_stream


def _frame(event: str, **body: object) -> list[str]:
    return [f"event: {event}", "data: " + json.dumps({"run_id": "r", **body}), ""]


async def _lines(*frames: list[str]) -> AsyncIterator[str]:
    for f in frames:
        for line in f:
            yield line


class _Clock:
    """Readings 0.0, 0.4, 1.0, ... one per call (seconds)."""

    def __init__(self, *ticks: float) -> None:
        self.ticks = list(ticks)

    def __call__(self) -> float:
        return self.ticks.pop(0)


async def test_sse_ttft_is_first_segment() -> None:
    frames = (
        _frame("run.started", message_id="u1"),
        [": heartbeat", ""],
        _frame("status", stage="writing"),
        _frame("segment", index=0, text="a"),
        _frame("segment", index=1, text="b"),
        _frame("done", message_id="m1", response_type="answer"),
    )
    # clock is read once per dispatched frame: started=0.0 is passed in; frames at 0.1 .. 1.5 s
    clock = _Clock(0.1, 0.2, 0.4, 1.0, 1.5)
    res = await read_turn_stream(_lines(*frames), 0.0, clock)
    assert res.ttft_ms == pytest.approx(400)
    assert res.latency_ms == pytest.approx(1500) and res.message_id == "m1"
    assert res.events == ["run.started", "status", "segment", "segment", "done"]


async def test_sse_abstain_has_no_ttft() -> None:
    frames = (
        _frame("run.started", message_id="u1"),
        _frame("done", message_id="m2", response_type="abstain"),
    )
    res = await read_turn_stream(_lines(*frames), 0.0, _Clock(0.1, 0.9))
    assert res.ttft_ms is None and res.latency_ms == pytest.approx(900) and res.response_type == "abstain"


async def test_sse_error_frame_ends_stream() -> None:
    res = await read_turn_stream(
        _lines(_frame("error", code="internal_error", retryable=True)), 0.0, _Clock(0.2)
    )
    assert res.error == "internal_error" and res.message_id is None


def _run(ttfts: list[float | None]) -> RunResult:
    import datetime as dt

    items = [
        ItemResult(f"i{k}", "c", "smoke", {"latency_ms": 5.0, **({} if t is None else {"ttft_ms": t})})
        for k, t in enumerate(ttfts)
    ]
    return RunResult(uuid.uuid4(), "smoke", "smoke", "smoke", "sha", dt.datetime.now(dt.UTC), items=items)


def test_ttft_summary_percentiles_skip_abstentions() -> None:
    run = _run([1000, 3000, None, 2000, 4000])
    assert eval_run.ttft_summary(run) == "ttft_ms p50 2000 p95 4000 (n=4)"
    assert eval_run.ttft_summary(_run([None])) is None


def test_ttft_lands_in_item_metrics() -> None:
    assert eval_run._extra(ItemOutput(latency_ms=10.0, ttft_ms=4.0)) == {"latency_ms": 10.0, "ttft_ms": 4.0}
    assert "ttft_ms" not in eval_run._extra(ItemOutput(latency_ms=10.0))


def test_stream_flag_requires_a_streaming_setup() -> None:
    http_setup = eval_run._load_setup("eval.pipeline_http:setup", stream=True)
    assert http_setup.keywords == {"stream": True}  # type: ignore[attr-defined]
    with pytest.raises(SystemExit) as e:
        eval_run._load_setup("eval.pipeline_retrieval:setup", stream=True)
    assert "does not support streaming" in str(e.value)
    assert eval_run._load_setup("eval.pipeline_retrieval:setup") is not None


def test_stream_flag_is_a_cli_option() -> None:
    with pytest.raises(SystemExit) as e:  # argparse: --help lists it, exit 0
        eval_run.main(["--help"])
    assert e.value.code == 0
