"""S2.14 AC3: Batch mode yields results identical in schema to sync mode (fake backend); the OpenAI backend
speaks the SDK's file/batch shapes (stubbed client, no network)."""

import io
import json
import pathlib
from types import SimpleNamespace
from typing import Any

import pytest
from eval.engine.batch import (
    BatchError,
    BatchRequest,
    BatchResponse,
    FakeBatchBackend,
    OpenAIBatchBackend,
    run_batch,
)
from eval.engine.cache import StageCache
from eval.engine.ledger import MemoryCostSource, SpendLedger
from eval.engine.runner import Engine, RunConfig

from tests.support.engine_kit import SUITE, Rig, stage_values


def _schema(v: Any) -> Any:
    if isinstance(v, dict):
        return {k: _schema(x) for k, x in sorted(v.items())}
    if isinstance(v, list):
        return [_schema(x) for x in v[:1]]
    return type(v).__name__


async def _no_sleep(_s: float) -> None:
    return None


async def test_batch_parity(tmp_path: pathlib.Path) -> None:
    sync_rig, batch_rig = Rig(), Rig()
    sync = await Engine(
        sync_rig.stages(batchable=True),
        ledger=SpendLedger(MemoryCostSource()),
        cache=StageCache(tmp_path / "c1"),
    ).run(SUITE, RunConfig("s", run_id="sync", runs_dir=tmp_path / "runs"))

    backend = FakeBatchBackend(polls_to_complete=2)
    batch = await Engine(
        batch_rig.stages(batchable=True),
        ledger=SpendLedger(MemoryCostSource()),
        cache=StageCache(tmp_path / "c2"),
        backend=backend,
        sleep=_no_sleep,
    ).run(SUITE, RunConfig("b", run_id="batch", mode="batch", runs_dir=tmp_path / "runs"))

    assert len(SUITE.items) == 3 and sync.status == batch.status == "completed"
    assert _schema(stage_values(batch.units)) == _schema(stage_values(sync.units))
    assert stage_values(batch.units) == stage_values(sync.units)  # the fake replies carry the same text
    assert {k: set(r) for k, r in batch.units.items()} == {k: set(r) for k, r in sync.units.items()}
    assert batch_rig.provider.requests == []  # generation and judge went through the batch backend only
    assert len(sync_rig.provider.requests) == 6
    assert [len(b) for b in backend.submitted] == [3, 3]  # one batch per batchable stage
    assert batch_rig.retrievals == 3  # the non-batchable stage ran sync


async def test_batch_failed_request_is_a_unit_error(tmp_path: pathlib.Path) -> None:
    def responder(r: BatchRequest) -> BatchResponse:
        if r.custom_id.startswith("q1"):
            return BatchResponse(r.custom_id, None, "http_400:invalid_request", 400)
        return BatchResponse(r.custom_id, {"output_text": "ok"})

    res = await Engine(
        Rig().stages(batchable=True),
        ledger=SpendLedger(MemoryCostSource()),
        backend=FakeBatchBackend(responder=responder),
        sleep=_no_sleep,
    ).run(SUITE, RunConfig("b", run_id="e", mode="batch", runs_dir=tmp_path))
    assert list(res.errors) == ["q1#r0"] and "http_400" in res.errors["q1#r0"]
    assert res.units["q0#r0"]["error"] is None


async def test_batch_resume_reattaches_to_submitted_batch(tmp_path: pathlib.Path) -> None:
    """A kill while a batch is in flight must not pay for a second submission."""
    backend = FakeBatchBackend(polls_to_complete=1)
    calls = {"n": 0}
    orig = backend.status

    async def dying_status(batch_id: str) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise KeyboardInterrupt  # killed while waiting for the first batch
        return await orig(batch_id)

    backend.status = dying_status  # type: ignore[method-assign]
    cfg = RunConfig("b", run_id="reattach", mode="batch", runs_dir=tmp_path)
    eng = Engine(
        Rig().stages(batchable=True), ledger=SpendLedger(MemoryCostSource()), backend=backend, sleep=_no_sleep
    )
    with pytest.raises(KeyboardInterrupt):
        await eng.run(SUITE, cfg)
    assert len(backend.submitted) == 1
    res = await eng.run(SUITE, cfg)
    assert res.status == "completed" and not res.errors
    assert len(backend.submitted) == 2  # the first (re-attached) generation batch + the judge batch only


async def test_run_batch_reattaches_and_times_out() -> None:
    be = FakeBatchBackend(polls_to_complete=0)
    reqs = [BatchRequest("a", {}), BatchRequest("b", {})]
    bid = await be.submit(reqs)
    out = await run_batch(be, reqs, batch_id=bid, sleep=_no_sleep)  # re-attach: no second submit
    assert set(out) == {"a", "b"} and len(be.submitted) == 1
    slow = FakeBatchBackend(polls_to_complete=10**6)
    with pytest.raises(BatchError, match="timed out"):
        await run_batch(slow, reqs, poll_s=10, timeout_s=30, sleep=_no_sleep)
    assert slow.cancelled


class _StubFiles:
    def __init__(self, store: dict[str, str]) -> None:
        self.store = store
        self.created: list[tuple[tuple[str, io.BytesIO], str]] = []

    async def create(self, *, file: tuple[str, io.BytesIO], purpose: str) -> SimpleNamespace:
        self.created.append((file, purpose))
        self.store["file_in"] = file[1].getvalue().decode()
        return SimpleNamespace(id="file_in")

    async def content(self, file_id: str) -> SimpleNamespace:
        return SimpleNamespace(text=self.store[file_id])


class _StubBatches:
    def __init__(self, store: dict[str, str]) -> None:
        self.store, self.calls = store, 0
        self.created: dict[str, Any] = {}

    async def create(self, **kw: Any) -> SimpleNamespace:
        self.created = kw
        return SimpleNamespace(id="batch_1")

    async def retrieve(self, batch_id: str) -> SimpleNamespace:
        self.calls += 1
        done = self.calls > 1
        if done:
            ids = [json.loads(ln)["custom_id"] for ln in self.store["file_in"].splitlines()]
            ok = {"status_code": 200}
            out = [  # reversed: output order is not input order
                {
                    "id": "r2",
                    "custom_id": ids[1],
                    "response": {**ok, "body": {"output_text": "B"}},
                    "error": None,
                },
                {
                    "id": "r1",
                    "custom_id": ids[0],
                    "response": {**ok, "body": {"output_text": "A"}},
                    "error": None,
                },
            ]
            err = {"id": "r3", "custom_id": "bad", "response": None, "error": {"code": "invalid_request"}}
            self.store["file_out"] = "\n".join(map(json.dumps, out))
            self.store["file_err"] = json.dumps(err)
        return SimpleNamespace(
            id=batch_id,
            status="completed" if done else "in_progress",
            output_file_id="file_out" if done else None,
            error_file_id="file_err" if done else None,
        )

    async def cancel(self, batch_id: str) -> None:
        return None


async def test_openai_backend_wire_shape() -> None:
    store: dict[str, str] = {}
    client = SimpleNamespace(files=_StubFiles(store), batches=_StubBatches(store))
    be = OpenAIBatchBackend(client, metadata={"run": "t"})
    reqs = [
        BatchRequest("u1", {"model": "m", "input": "x"}),
        BatchRequest("u2", {"model": "m", "input": "y"}),
    ]
    res = await run_batch(be, reqs, sleep=_no_sleep)
    first = json.loads(store["file_in"].splitlines()[0])
    assert first == {
        "custom_id": "u1",
        "method": "POST",
        "url": "/v1/responses",
        "body": {"model": "m", "input": "x"},
    }
    assert client.files.created[0][1] == "batch"
    assert client.batches.created == {
        "input_file_id": "file_in",
        "endpoint": "/v1/responses",
        "completion_window": "24h",
        "metadata": {"run": "t"},
    }
    assert res["u1"].body == {"output_text": "A"} and res["u2"].body == {"output_text": "B"}  # matched by id
    assert not res["bad"].ok and res["bad"].error == "invalid_request"
