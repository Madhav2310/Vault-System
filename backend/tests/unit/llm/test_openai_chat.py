"""OpenAI Responses adapter over a mocked HTTP transport (no network): wire request and parsing (S0.23)."""

import json
from collections.abc import Callable

import httpx2
import openai
import pytest
from pydantic import BaseModel

from app.llm import errors
from app.llm.providers.openai_chat import OpenAIResponsesProvider, build_params, strict_schema
from app.llm.types import ChatRequest, ProviderFinal, SearchResult, TextDelta

Handler = Callable[[httpx2.Request], httpx2.Response]


class Inner(BaseModel):
    x: int = 0


class Out(BaseModel):
    answer: str
    inner: Inner
    note: str | None = None


REQ = ChatRequest(
    model="gpt-6-luna",
    system="sys",
    messages=[{"role": "user", "content": "q"}],
    max_tokens=256,
    effort="low",
    output_schema=Out.model_json_schema(),
)
USAGE = {
    "input_tokens": 1000,
    "input_tokens_details": {"cached_tokens": 300, "cache_write_tokens": 200},
    "output_tokens": 80,
    "output_tokens_details": {"reasoning_tokens": 50},
    "total_tokens": 1080,
}


def _provider(handler: Handler, seen: list[httpx2.Request]) -> OpenAIResponsesProvider:
    def h(req: httpx2.Request) -> httpx2.Response:
        seen.append(req)
        return handler(req)

    client = openai.AsyncOpenAI(
        api_key="k",
        max_retries=0,
        http_client=openai.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(h)),
    )
    return OpenAIResponsesProvider(None, 5.0, client=client)


def _response(
    content: list[dict[str, object]] | None = None, status: str = "completed", **extra: object
) -> dict[str, object]:
    content = content if content is not None else [{"type": "output_text", "text": "{}", "annotations": []}]
    return {
        "id": "resp_1",
        "object": "response",
        "created_at": 0,
        "model": "gpt-6-luna",
        "status": status,
        "output": [
            {"type": "reasoning", "id": "rs_1", "summary": []},
            {
                "type": "message",
                "id": "msg_1",
                "role": "assistant",
                "status": "completed",
                "content": content,
            },
        ],
        "usage": USAGE,
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
        **extra,
    }


def test_request_shape() -> None:
    p = build_params(REQ)
    assert p["reasoning"] == {"effort": "low"} and p["max_output_tokens"] == 256 and p["store"] is False
    assert p["instructions"] == "sys" and p["input"] == [{"role": "user", "content": "q"}]
    assert not {"temperature", "top_p", "service_tier"} & p.keys()
    fmt = p["text"]["format"]
    assert fmt["type"] == "json_schema" and fmt["strict"] is True
    schema = fmt["schema"]
    assert schema["additionalProperties"] is False and schema["required"] == ["answer", "inner", "note"]
    assert schema["$defs"]["Inner"]["additionalProperties"] is False
    assert schema["$defs"]["Inner"]["required"] == ["x"]
    assert "default" not in json.dumps(schema) and "title" not in json.dumps(schema)
    assert strict_schema({"type": "string"}) == {"type": "string"}
    assert build_params(ChatRequest("m", "s", [], 5, "none", service_tier="flex"))["service_tier"] == "flex"
    with pytest.raises(errors.RequestConflictError):
        build_params(ChatRequest("m", "s", [], 5, "low", search_results=(SearchResult("a", "b", ("c",)),)))


async def test_request_on_the_wire() -> None:
    seen: list[httpx2.Request] = []
    r = await _provider(lambda r: httpx2.Response(200, json=_response()), seen).complete(REQ)
    body = json.loads(seen[0].content)
    assert seen[0].url.path.endswith("/responses") and body["text"]["format"]["strict"] is True
    assert body["reasoning"] == {"effort": "low"} and r.text == "{}" and r.stop_reason == "end_turn"


async def test_usage_mapping() -> None:
    r = await _provider(lambda r: httpx2.Response(200, json=_response()), []).complete(REQ)
    u = r.usage
    # input_tokens (1000) includes the 300 cache reads and 200 cache writes; output (80) includes reasoning
    assert (u.input_tokens, u.cache_read_tokens, u.cache_write_tokens, u.output_tokens) == (500, 300, 200, 80)


def _sse(*events: dict[str, object]) -> bytes:
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


async def test_stream_events() -> None:
    final = _response()
    body = _sse(
        {"type": "response.created", "sequence_number": 0, "response": _response(status="in_progress")},
        *[
            {
                "type": "response.output_text.delta",
                "sequence_number": i + 1,
                "item_id": "msg_1",
                "output_index": 1,
                "content_index": 0,
                "delta": d,
                "logprobs": [],
            }
            for i, d in enumerate(["Hel", "lo"])
        ],
        {"type": "response.completed", "sequence_number": 9, "response": final},
    )
    seen: list[httpx2.Request] = []
    p = _provider(
        lambda r: httpx2.Response(200, content=body, headers={"content-type": "text/event-stream"}), seen
    )
    events = [e async for e in p.stream(REQ)]
    assert json.loads(seen[0].content)["stream"] is True
    assert [e.text for e in events if isinstance(e, TextDelta)] == ["Hel", "lo"]
    last = events[-1]
    assert isinstance(last, ProviderFinal) and last.stop_reason == "end_turn"
    assert (last.usage.input_tokens, last.usage.output_tokens) == (500, 80)
    assert sum(isinstance(e, ProviderFinal) for e in events) == 1


async def test_refusal_and_incomplete() -> None:
    refusal = _response([{"type": "refusal", "refusal": "no"}])
    r = await _provider(lambda r: httpx2.Response(200, json=refusal), []).complete(REQ)
    assert r.stop_reason == "refusal" and r.text == ""
    inc = _response(status="incomplete", incomplete_details={"reason": "max_output_tokens"})
    r = await _provider(lambda r: httpx2.Response(200, json=inc), []).complete(REQ)
    assert r.stop_reason == "max_tokens"  # the gateway turns this into TruncatedError
    cf = _response(status="incomplete", incomplete_details={"reason": "content_filter"})
    r = await _provider(lambda r: httpx2.Response(200, json=cf), []).complete(REQ)
    assert (r.stop_reason, r.refusal_category) == ("refusal", "content_filter")


@pytest.mark.parametrize(
    ("status", "headers", "exc", "retry_after"),
    [
        (429, {"retry-after": "2"}, errors.RateLimitedError, 2.0),
        (503, {}, errors.ProviderServerError, None),
        (400, {}, errors.BadRequestError, None),
        (401, {}, errors.ProviderAuthError, None),
    ],
)
async def test_error_mapping(
    status: int, headers: dict[str, str], exc: type[errors.ProviderError], retry_after: float | None
) -> None:
    seen: list[httpx2.Request] = []
    body = {"error": {"message": "m", "type": "t", "code": "c"}}
    p = _provider(lambda r: httpx2.Response(status, json=body, headers=headers), seen)
    with pytest.raises(exc) as e:
        await p.complete(REQ)
    assert e.value.retry_after == retry_after and e.value.retryable == exc.retryable
    assert len(seen) == 1  # SDK retries are off; the gateway owns retries

    def boom(r: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("t", request=r)

    with pytest.raises(errors.ProviderTimeoutError):
        await _provider(boom, []).complete(REQ)
