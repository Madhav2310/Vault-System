"""Batch mode: a pluggable backend for the OpenAI Batch API (50% price, 24 h window; research 06 §4).

`BatchBackend` is the seam. `FakeBatchBackend` is in-memory (tests, dry runs). `OpenAIBatchBackend` speaks
the real API through an injected client; this module never imports or constructs a provider SDK client (the
architecture rule: only `app/llm/providers` does), so the caller passes an `openai.AsyncOpenAI`-shaped object.

Real API shape, checked against openai 3.49.0 (`AsyncBatches.create(completion_window="24h", endpoint=...,
input_file_id=...)`, `retrieve`, `cancel`; `AsyncFiles.create(file=, purpose="batch")`, `.content(id)`;
`Batch.status` in validating|failed|in_progress|finalizing|completed|expired|cancelling|cancelled,
`output_file_id`, `error_file_id`). Input JSONL line: `{"custom_id", "method": "POST", "url": "/v1/responses",
"body": {...}}`; output line: `{"id", "custom_id", "response": {"status_code", "request_id", "body"},
"error"}`. Output order is not input order, so results are matched by `custom_id`.
"""

from __future__ import annotations

import asyncio
import io
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

BATCH_DISCOUNT = 0.5
ENDPOINT = "/v1/responses"

BatchState = Literal["in_progress", "completed", "failed", "expired", "cancelled"]
TERMINAL: frozenset[str] = frozenset({"completed", "failed", "expired", "cancelled"})

__all__ = [
    "BATCH_DISCOUNT",
    "BatchBackend",
    "BatchError",
    "BatchRequest",
    "BatchResponse",
    "FakeBatchBackend",
    "OpenAIBatchBackend",
    "run_batch",
]


class BatchError(RuntimeError):
    pass


@dataclass(frozen=True)
class BatchRequest:
    custom_id: str  # unique within the batch; the engine uses the unit id
    body: Mapping[str, Any]  # a /v1/responses request body (model, input, ...)


@dataclass(frozen=True)
class BatchResponse:
    custom_id: str
    body: Mapping[str, Any] | None = None  # the Responses API response body when ok
    error: str | None = None  # code/message only, no content
    status_code: int = 200

    @property
    def ok(self) -> bool:
        return self.error is None and self.body is not None and 200 <= self.status_code < 300


class BatchBackend(Protocol):
    async def submit(self, requests: Sequence[BatchRequest]) -> str: ...

    async def status(self, batch_id: str) -> BatchState: ...

    async def results(self, batch_id: str) -> dict[str, BatchResponse]:
        """Responses by custom_id for a completed batch; failed requests are included with `error` set."""
        ...

    async def cancel(self, batch_id: str) -> None: ...


# --- in-memory backend ------------------------------------------------------------------------------

Responder = Callable[[BatchRequest], BatchResponse]


def default_responder(req: BatchRequest) -> BatchResponse:
    return BatchResponse(
        req.custom_id,
        {"output_text": "ok", "usage": {"input_tokens": 100, "output_tokens": 20}},
    )


@dataclass
class FakeBatchBackend:
    """Completes after `polls_to_complete` status() calls. `submitted` records every batch for assertions."""

    responder: Responder = default_responder
    polls_to_complete: int = 1
    submitted: list[list[BatchRequest]] = field(default_factory=list)
    cancelled: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._polls: dict[str, int] = {}

    async def submit(self, requests: Sequence[BatchRequest]) -> str:
        ids = [r.custom_id for r in requests]
        if len(set(ids)) != len(ids):
            raise BatchError("duplicate custom_id in batch")
        self.submitted.append(list(requests))
        bid = f"fakebatch_{len(self.submitted)}"
        self._polls[bid] = 0
        return bid

    async def status(self, batch_id: str) -> BatchState:
        if batch_id in self.cancelled:
            return "cancelled"
        self._polls[batch_id] += 1
        return "completed" if self._polls[batch_id] > self.polls_to_complete else "in_progress"

    async def results(self, batch_id: str) -> dict[str, BatchResponse]:
        idx = int(batch_id.rsplit("_", 1)[1]) - 1
        return {r.custom_id: self.responder(r) for r in self.submitted[idx]}

    async def cancel(self, batch_id: str) -> None:
        self.cancelled.append(batch_id)


# --- OpenAI backend ---------------------------------------------------------------------------------


class OpenAIBatchBackend:
    """`client` is an `openai.AsyncOpenAI` (built by app.llm.providers, not here). Untested live: no calls
    are made in this repo's tests; `tests/integration/eval/test_engine_batch.py` drives it with a stub client
    that mirrors the SDK signatures."""

    def __init__(self, client: Any, *, endpoint: str = ENDPOINT, metadata: Mapping[str, str] | None = None):
        self._c, self._endpoint, self._metadata = client, endpoint, dict(metadata or {})
        self._batches: dict[str, Any] = {}

    async def submit(self, requests: Sequence[BatchRequest]) -> str:
        ids = [r.custom_id for r in requests]
        if len(set(ids)) != len(ids):
            raise BatchError("duplicate custom_id in batch")
        lines = [
            json.dumps(
                {"custom_id": r.custom_id, "method": "POST", "url": self._endpoint, "body": dict(r.body)},
                sort_keys=True,
                ensure_ascii=False,
            )
            for r in requests
        ]
        payload = io.BytesIO(("\n".join(lines) + "\n").encode("utf-8"))
        f = await self._c.files.create(file=("batch_input.jsonl", payload), purpose="batch")
        kw: dict[str, Any] = {}
        if self._metadata:
            kw["metadata"] = self._metadata
        b = await self._c.batches.create(
            input_file_id=f.id, endpoint=self._endpoint, completion_window="24h", **kw
        )
        return str(b.id)

    async def status(self, batch_id: str) -> BatchState:
        b = await self._c.batches.retrieve(batch_id)
        self._batches[batch_id] = b
        s = str(b.status)
        if s == "completed":
            return "completed"
        if s in ("failed", "expired", "cancelled"):
            return s  # type: ignore[return-value]
        return "in_progress"  # validating | in_progress | finalizing | cancelling

    async def results(self, batch_id: str) -> dict[str, BatchResponse]:
        b = self._batches.get(batch_id) or await self._c.batches.retrieve(batch_id)
        out: dict[str, BatchResponse] = {}
        for file_id in (b.output_file_id, b.error_file_id):
            if not file_id:
                continue
            raw = await self._c.files.content(file_id)
            for line in raw.text.splitlines():
                if line.strip():
                    r = parse_output_line(json.loads(line))
                    out.setdefault(r.custom_id, r)
        return out

    async def cancel(self, batch_id: str) -> None:
        await self._c.batches.cancel(batch_id)


def parse_output_line(d: Mapping[str, Any]) -> BatchResponse:
    cid = str(d["custom_id"])
    resp, err = d.get("response"), d.get("error")
    if err:
        return BatchResponse(cid, None, f"{err.get('code') or 'error'}", 0)
    code = int(resp.get("status_code", 0)) if resp else 0
    body = resp.get("body") if resp else None
    if not (200 <= code < 300) or body is None:
        etype = (body or {}).get("error", {}).get("type") if isinstance(body, dict) else None
        return BatchResponse(cid, None, f"http_{code}:{etype or 'error'}", code)
    return BatchResponse(cid, body, None, code)


# --- driver -----------------------------------------------------------------------------------------


async def run_batch(
    backend: BatchBackend,
    requests: Sequence[BatchRequest],
    *,
    batch_id: str | None = None,
    on_submitted: Callable[[str], Awaitable[None]] | None = None,
    poll_s: float = 30.0,
    timeout_s: float = 24 * 3600 + 600,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> dict[str, BatchResponse]:
    """Submit (or, with `batch_id`, re-attach to a batch submitted before a kill), wait, return responses.
    `on_submitted` fires right after submission so the id can be persisted before the long wait."""
    if batch_id is None:
        batch_id = await backend.submit(requests)
        if on_submitted:
            await on_submitted(batch_id)
    waited = 0.0
    while True:
        st = await backend.status(batch_id)
        if st == "completed":
            return await backend.results(batch_id)
        if st in TERMINAL:
            raise BatchError(f"batch {batch_id} ended {st}")
        if waited >= timeout_s:
            await backend.cancel(batch_id)
            raise BatchError(f"batch {batch_id} timed out")
        await sleep(poll_s)
        waited += poll_s
