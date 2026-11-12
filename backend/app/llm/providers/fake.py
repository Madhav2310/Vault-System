"""Deterministic in-process provider for tests: no network. Each call consumes the next scripted step
(a reply or an error); when the script is empty the default reply is used. Every request is recorded."""

import asyncio
import hashlib
import json
import re
from collections import deque
from collections.abc import AsyncGenerator, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.llm import errors
from app.llm.types import (
    ChatRequest,
    ChatResponse,
    Citation,
    CitationDelta,
    EmbeddingResponse,
    ProviderFinal,
    StreamEvent,
    TextDelta,
    TextPart,
    Usage,
)


@dataclass(frozen=True)
class FakeReply:
    parts: tuple[TextPart, ...] = (TextPart("ok"),)
    stop_reason: str = "end_turn"
    usage: Usage = field(default_factory=lambda: Usage(input_tokens=100, output_tokens=20))
    refusal_category: str | None = None
    fail_after_events: int | None = None  # stream only: raise `fail_with` after this many events
    fail_with: errors.ProviderError | None = None


Step = FakeReply | errors.ProviderError


# --- step builders (error injection) ---------------------------------------------------------------


def rate_limited(retry_after: float | None = 1.0) -> errors.RateLimitedError:
    return errors.RateLimitedError(status_code=429, retry_after=retry_after)


def overloaded(retry_after: float | None = None) -> errors.OverloadedError:
    return errors.OverloadedError(status_code=529, retry_after=retry_after)


def timeout() -> errors.ProviderTimeoutError:
    return errors.ProviderTimeoutError()


def bad_request() -> errors.BadRequestError:
    return errors.BadRequestError("400 invalid_request_error", status_code=400)


def text(value: str, **kw: Any) -> FakeReply:
    return FakeReply(parts=(TextPart(value),), **kw)


def structured(obj: Any, **kw: Any) -> FakeReply:
    return text(json.dumps(obj), **kw)


def refusal(category: str | None = "cyber") -> FakeReply:
    return FakeReply(parts=(), stop_reason="refusal", refusal_category=category)


def max_tokens(partial: str = "partial answer") -> FakeReply:
    return text(partial, stop_reason="max_tokens")


def cited(*parts: tuple[str, Sequence[Citation]], **kw: Any) -> FakeReply:
    return FakeReply(parts=tuple(TextPart(t, tuple(c)) for t, c in parts), **kw)


@dataclass
class FakeProvider:
    script: Iterable[Step] = ()
    latency_s: float = 0.0
    default: FakeReply = field(default_factory=FakeReply)
    name: str = "fake"
    requests: list[ChatRequest] = field(default_factory=list)
    embed_calls: list[tuple[str, tuple[str, ...], int]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._queue: deque[Step] = deque(self.script)

    def push(self, *steps: Step) -> None:
        self._queue.extend(steps)

    async def _next(self) -> FakeReply:
        step = self._queue.popleft() if self._queue else self.default
        if self.latency_s:
            await asyncio.sleep(self.latency_s)
        if isinstance(step, errors.ProviderError):
            raise step
        return step

    async def complete(self, req: ChatRequest) -> ChatResponse:
        self.requests.append(req)
        r = await self._next()
        return ChatResponse(r.parts, r.stop_reason, r.usage, r.refusal_category, req.model)

    async def stream(self, req: ChatRequest) -> AsyncGenerator[StreamEvent, None]:
        self.requests.append(req)
        r = await self._next()
        sent = 0
        for i, part in enumerate(r.parts):
            events: list[StreamEvent] = [TextDelta(i, w) for w in re.findall(r"\s*\S+\s*", part.text)]
            events += [CitationDelta(i, c) for c in part.citations]
            for ev in events:
                if r.fail_after_events is not None and sent >= r.fail_after_events:
                    raise r.fail_with or errors.ProviderConnectionError()
                yield ev
                sent += 1
        yield ProviderFinal(r.stop_reason, r.usage, r.refusal_category, req.model)

    async def embed(self, model: str, texts: Sequence[str], dimensions: int) -> EmbeddingResponse:
        self.embed_calls.append((model, tuple(texts), dimensions))
        step = self._queue.popleft() if self._queue else None
        if isinstance(step, errors.ProviderError):
            raise step
        vectors = [_vector(t, dimensions) for t in texts]
        return EmbeddingResponse(vectors, Usage(input_tokens=sum(len(t.split()) for t in texts)))


def _vector(text_: str, dims: int) -> list[float]:
    seed = hashlib.sha256(text_.encode()).digest()
    return [(seed[i % len(seed)] - 127.5) / 127.5 for i in range(dims)]
