"""OpenAI Responses API chat adapter.

- SDK retries are off (`max_retries=0`): the gateway owns retries and writes one `llm_calls` row per attempt.
- Structured output is `text.format` = strict `json_schema`; reasoning effort is always explicit (the
  API default is `medium`); `temperature`/`top_p` are never sent. `store=False`: nothing is retained.
- Usage: OpenAI's `input_tokens` includes cached reads and cache writes, so uncached = total - both.
  `output_tokens` already includes reasoning tokens.
- Citations are application-managed: provider-native search results are rejected here.
"""

from collections.abc import AsyncGenerator
from typing import Any

import openai
from openai.types.responses import Response, ResponseUsage

from app.llm import errors
from app.llm.providers.openai import map_error
from app.llm.types import (
    ChatRequest,
    ChatResponse,
    ProviderFinal,
    StreamEvent,
    TextDelta,
    TextPart,
    Usage,
)

SCHEMA_NAME = "output"


def strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Pydantic JSON schema → OpenAI strict-mode schema: every object closed (`additionalProperties: false`)
    with all properties required; `default` and `title` annotations dropped. `$defs`/`$ref` pass through."""

    def walk(node: Any) -> Any:
        if isinstance(node, list):
            return [walk(x) for x in node]
        if not isinstance(node, dict):
            return node
        out: dict[str, Any] = {}
        for k, v in node.items():
            if k in ("default", "title"):  # annotation keywords of this schema node
                continue
            if k in ("properties", "$defs") and isinstance(v, dict):  # name → schema maps: keep every name
                out[k] = {name: walk(sub) for name, sub in v.items()}
            else:
                out[k] = walk(v)
        props = out.get("properties")
        if isinstance(props, dict):
            out["additionalProperties"] = False
            out["required"] = list(props)
        return out

    walked: dict[str, Any] = walk(schema)
    return walked


def build_params(req: ChatRequest) -> dict[str, Any]:
    """Request body for `responses.create`. Pure: unit-testable without a client."""
    if req.search_results:
        raise errors.RequestConflictError("provider-native search results are not used with OpenAI (§8.5)")
    params: dict[str, Any] = {
        "model": req.model,
        "instructions": req.system,
        "input": [dict(m) for m in req.messages],
        "max_output_tokens": req.max_tokens,
        "reasoning": {"effort": req.effort},
        "store": False,
    }
    if req.output_schema is not None:
        params["text"] = {
            "format": {
                "type": "json_schema",
                "name": SCHEMA_NAME,
                "strict": True,
                "schema": strict_schema(req.output_schema),
            }
        }
    if req.cache_mode is not None:  # "explicit" without breakpoints = no caching, hence no cache-write charge
        params["prompt_cache_options"] = {"mode": req.cache_mode}
    if req.service_tier is not None:
        params["service_tier"] = req.service_tier
    return params


def _usage(u: ResponseUsage | None) -> Usage:
    if u is None:
        return Usage()
    cached = u.input_tokens_details.cached_tokens
    written = u.input_tokens_details.cache_write_tokens
    return Usage(
        input_tokens=max(0, u.input_tokens - cached - written),
        output_tokens=u.output_tokens,
        cache_read_tokens=cached,
        cache_write_tokens=written,
    )


def _failed(resp: Response) -> errors.ProviderError:
    code = resp.error.code if resp.error else None
    if code == "rate_limit_exceeded":
        return errors.RateLimitedError(status_code=429)
    if code in (None, "server_error"):
        return errors.ProviderServerError(status_code=500)
    return errors.BadRequestError(str(code))


def _stop_reason(resp: Response) -> tuple[str, str | None]:
    """(stop_reason, refusal_category) in the gateway's vocabulary."""
    if resp.status == "failed":
        raise _failed(resp)
    if resp.status == "incomplete":
        reason = resp.incomplete_details.reason if resp.incomplete_details else None
        if reason == "content_filter":
            return "refusal", "content_filter"
        return "max_tokens", None
    for item in resp.output:
        if item.type == "message" and any(c.type == "refusal" for c in item.content):
            return "refusal", None
    return "end_turn", None


def to_response(resp: Response) -> ChatResponse:
    stop, category = _stop_reason(resp)
    parts = tuple(
        TextPart(c.text)
        for item in resp.output
        if item.type == "message"
        for c in item.content
        if c.type == "output_text"
    )
    return ChatResponse(
        parts=parts,
        stop_reason=stop,
        usage=_usage(resp.usage),
        refusal_category=category,
        resolved_model=resp.model,
    )


class OpenAIResponsesProvider:
    name = "openai"

    def __init__(self, api_key: str | None, timeout_s: float, client: openai.AsyncOpenAI | None = None):
        self._api_key, self._timeout_s, self._client = api_key, timeout_s, client

    @property
    def client(self) -> openai.AsyncOpenAI:
        if self._client is None:  # lazy: a missing key must not break app start (/readyz reports it)
            self._client = openai.AsyncOpenAI(api_key=self._api_key, max_retries=0, timeout=self._timeout_s)
        return self._client

    async def complete(self, req: ChatRequest) -> ChatResponse:
        params = build_params(req)
        try:
            resp = await self.client.responses.create(**params)
        except openai.APIError as e:
            raise map_error(e) from e
        return to_response(resp)

    async def stream(self, req: ChatRequest) -> AsyncGenerator[StreamEvent, None]:
        params = build_params(req)
        final: ProviderFinal | None = None
        try:
            # `async with` closes the HTTP stream deterministically, also when the consumer stops early.
            async with await self.client.responses.create(**params, stream=True) as events:
                async for ev in events:
                    if ev.type == "response.output_text.delta":
                        yield TextDelta(ev.output_index, ev.delta)
                    elif ev.type in ("response.completed", "response.incomplete", "response.failed"):
                        r = to_response(ev.response)
                        final = ProviderFinal(
                            stop_reason=r.stop_reason,
                            usage=r.usage,
                            refusal_category=r.refusal_category,
                            resolved_model=r.resolved_model,
                        )
                        break
                    elif ev.type == "error":
                        raise errors.ProviderServerError(ev.code or "stream error")
        except openai.APIError as e:
            raise map_error(e) from e
        if final is None:
            raise errors.ProviderConnectionError("stream ended without a terminal event")
        yield final
