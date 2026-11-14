"""Anthropic Messages API adapter (anthropic SDK 2.x, API version 2026-10-21).

- SDK retries are off (`max_retries=0`): the gateway owns retries and writes one `llm_calls` row per attempt.
- Native OpenTelemetry with `content_mode=metadata_only`, set explicitly so env vars cannot flip it.
- No `temperature`/`top_p`/`top_k` (400 on 2026-10-21) and no `thinking` param (5.5 models run adaptive).
- Evidence goes in `search_result` blocks, one text block per sentence, citations enabled.
"""

from collections.abc import AsyncGenerator
from typing import Any

import anthropic
from anthropic.types import (
    CitationsDelta as SdkCitationsDelta,
)
from anthropic.types import (
    CitationsSearchResultLocation,
    Message,
    RawContentBlockDeltaEvent,
    TextBlock,
)
from anthropic.types import (
    TextDelta as SdkTextDelta,
)
from anthropic.types import (
    Usage as SdkUsage,
)

from app.llm import errors
from app.llm.types import (
    ChatRequest,
    ChatResponse,
    Citation,
    CitationDelta,
    ProviderFinal,
    StreamEvent,
    TextDelta,
    TextPart,
    Usage,
)

OTEL_OPTION: anthropic.OpenTelemetryClientOption = {"traces": {"content_mode": "metadata_only"}}


def build_params(req: ChatRequest) -> dict[str, Any]:
    """Request body for `messages.create` / `messages.stream`. Pure: unit-testable without a client."""
    if req.effort == "none":
        raise errors.RequestConflictError("effort 'none' is not supported by the Anthropic adapter")
    output_config: dict[str, Any] = {"effort": req.effort}
    if req.output_schema is not None:
        output_config["format"] = {
            "type": "json_schema",
            "schema": anthropic.transform_schema(req.output_schema),
        }
    messages: list[dict[str, Any]] = [dict(m) for m in req.messages]
    if req.search_results:
        last = messages[-1] if messages else None
        if last is None or last["role"] != "user" or not isinstance(last["content"], str):
            raise errors.RequestConflictError("search results need a final user message with text content")
        blocks: list[dict[str, Any]] = [
            {
                "type": "search_result",
                "source": sr.source,
                "title": sr.title,
                "content": [{"type": "text", "text": s} for s in sr.sentences],
                "citations": {"enabled": True},
            }
            for sr in req.search_results
        ]
        last["content"] = [*blocks, {"type": "text", "text": last["content"]}]
    return {
        "model": req.model,
        "max_tokens": req.max_tokens,
        "system": req.system,
        "messages": messages,
        "output_config": output_config,
    }


def map_error(e: anthropic.APIError) -> errors.ProviderError:
    """SDK exception → typed gateway error. Order matters: timeout is a subclass of connection error."""
    if isinstance(e, anthropic.APITimeoutError):
        return errors.ProviderTimeoutError()
    if isinstance(e, anthropic.APIConnectionError):
        return errors.ProviderConnectionError()
    if isinstance(e, anthropic.APIStatusError):
        code, ra = e.status_code, errors.parse_retry_after(e.response.headers)
        if isinstance(e, anthropic.RateLimitError):
            return errors.RateLimitedError(status_code=code, retry_after=ra)
        if code == 529 or e.type == "overloaded_error":
            return errors.OverloadedError(status_code=code, retry_after=ra)
        if code >= 500 or code in (408, 409):
            return errors.ProviderServerError(status_code=code, retry_after=ra)
        if code in (401, 403):
            return errors.ProviderAuthError(status_code=code)
        return errors.BadRequestError(f"{code} {e.type}", status_code=code)
    return errors.ProviderError(type(e).__name__)


def _usage(u: SdkUsage) -> Usage:
    return Usage(
        input_tokens=u.input_tokens,
        output_tokens=u.output_tokens,
        cache_read_tokens=u.cache_read_input_tokens or 0,
        cache_write_tokens=u.cache_creation_input_tokens or 0,
    )


def _citation(c: CitationsSearchResultLocation) -> Citation:
    return Citation(
        search_result_index=c.search_result_index,
        start_block_index=c.start_block_index,
        end_block_index=c.end_block_index,
        cited_text=c.cited_text,
        source=c.source,
        title=c.title,
    )


def to_response(msg: Message) -> ChatResponse:
    parts = tuple(
        TextPart(
            b.text,
            tuple(_citation(c) for c in (b.citations or []) if isinstance(c, CitationsSearchResultLocation)),
        )
        for b in msg.content
        if isinstance(b, TextBlock)
    )
    return ChatResponse(
        parts=parts,
        stop_reason=msg.stop_reason or "unknown",
        usage=_usage(msg.usage),
        refusal_category=msg.stop_details.category if msg.stop_details else None,
    )


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str | None, timeout_s: float, client: anthropic.AsyncAnthropic | None = None):
        self._api_key, self._timeout_s, self._client = api_key, timeout_s, client

    @property
    def client(self) -> anthropic.AsyncAnthropic:
        if self._client is None:  # lazy: a missing key must not break app start (/readyz reports it)
            self._client = anthropic.AsyncAnthropic(
                api_key=self._api_key, max_retries=0, timeout=self._timeout_s, open_telemetry=OTEL_OPTION
            )
        return self._client

    async def complete(self, req: ChatRequest) -> ChatResponse:
        try:
            msg = await self.client.messages.create(**build_params(req))
        except anthropic.APIError as e:
            raise map_error(e) from e
        return to_response(msg)

    async def stream(self, req: ChatRequest) -> AsyncGenerator[StreamEvent, None]:
        try:
            async with self.client.messages.stream(**build_params(req)) as s:
                async for ev in s:
                    if not isinstance(ev, RawContentBlockDeltaEvent):
                        continue
                    d = ev.delta
                    if isinstance(d, SdkTextDelta):
                        yield TextDelta(ev.index, d.text)
                    elif isinstance(d, SdkCitationsDelta) and isinstance(
                        d.citation, CitationsSearchResultLocation
                    ):
                        yield CitationDelta(ev.index, _citation(d.citation))
                final = await s.get_final_message()
        except anthropic.APIError as e:
            raise map_error(e) from e
        r = to_response(final)
        yield ProviderFinal(stop_reason=r.stop_reason, usage=r.usage, refusal_category=r.refusal_category)
