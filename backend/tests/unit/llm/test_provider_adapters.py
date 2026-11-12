"""Provider adapters exercised through the real SDK clients over a mocked HTTP transport (no network).
Checks the wire request we send and the parsing of canned API responses / SSE streams."""

import json
from collections.abc import Callable

import anthropic
import httpx2
import openai
import pytest

from app.llm import errors
from app.llm.providers.anthropic import AnthropicProvider
from app.llm.providers.openai import OpenAIEmbeddingProvider
from app.llm.types import ChatRequest, CitationDelta, ProviderFinal, SearchResult, TextDelta

Handler = Callable[[httpx2.Request], httpx2.Response]

CITATION = {
    "type": "search_result_location",
    "cited_text": "First sentence.",
    "source": "chunk-1",
    "title": "Doc A",
    "search_result_index": 0,
    "start_block_index": 0,
    "end_block_index": 1,
}
USAGE = {
    "input_tokens": 50,
    "output_tokens": 7,
    "cache_read_input_tokens": 30,
    "cache_creation_input_tokens": 0,
}
REQ = ChatRequest(
    model="claude-sonnet-5-5",
    system="sys",
    messages=[{"role": "user", "content": "q"}],
    max_tokens=256,
    effort="low",
    search_results=(SearchResult("chunk-1", "Doc A", ("First sentence.", "Second.")),),
)


def _anthropic(handler: Handler, seen: list[httpx2.Request]) -> AnthropicProvider:
    def h(req: httpx2.Request) -> httpx2.Response:
        seen.append(req)
        return handler(req)

    client = anthropic.AsyncAnthropic(
        api_key="test-key",
        max_retries=0,
        http_client=anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(h)),
    )
    return AnthropicProvider(None, 5.0, client=client)


def _message(stop_reason: str = "end_turn", **extra: object) -> dict[str, object]:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-5-5",
        "content": [{"type": "text", "text": "Claim.", "citations": [CITATION]}],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": USAGE,
        **extra,
    }


async def test_anthropic_complete_request_and_response() -> None:
    seen: list[httpx2.Request] = []
    p = _anthropic(lambda r: httpx2.Response(200, json=_message()), seen)
    resp = await p.complete(REQ)
    body = json.loads(seen[0].content)
    assert seen[0].headers["anthropic-version"] == "2026-10-21"
    assert body["output_config"] == {"effort": "low"}
    assert not {"temperature", "top_p", "top_k", "thinking"} & body.keys()
    sr = body["messages"][0]["content"][0]
    assert sr["type"] == "search_result" and sr["citations"] == {"enabled": True}
    assert [b["text"] for b in sr["content"]] == ["First sentence.", "Second."]
    assert resp.text == "Claim." and resp.stop_reason == "end_turn"
    (c,) = resp.parts[0].citations
    assert (c.search_result_index, c.start_block_index, c.end_block_index, c.cited_text) == (
        0,
        0,
        1,
        "First sentence.",
    )
    assert (resp.usage.input_tokens, resp.usage.cache_read_tokens, resp.usage.cache_write_tokens) == (
        50,
        30,
        0,
    )


async def test_anthropic_refusal_category() -> None:
    msg = _message("refusal", stop_details={"type": "refusal", "category": "cyber", "explanation": None})
    p = _anthropic(lambda r: httpx2.Response(200, json=msg), [])
    resp = await p.complete(REQ)
    assert (resp.stop_reason, resp.refusal_category) == ("refusal", "cyber")


@pytest.mark.parametrize(
    ("status", "headers", "etype", "exc", "retry_after"),
    [
        (429, {"retry-after": "2"}, "rate_limit_error", errors.RateLimitedError, 2.0),
        (529, {}, "overloaded_error", errors.OverloadedError, None),
        (500, {}, "api_error", errors.ProviderServerError, None),
        (400, {}, "invalid_request_error", errors.BadRequestError, None),
        (401, {}, "authentication_error", errors.ProviderAuthError, None),
    ],
)
async def test_anthropic_error_mapping(
    status: int,
    headers: dict[str, str],
    etype: str,
    exc: type[errors.ProviderError],
    retry_after: float | None,
) -> None:
    seen: list[httpx2.Request] = []
    body = {"type": "error", "error": {"type": etype, "message": "m"}}
    p = _anthropic(lambda r: httpx2.Response(status, json=body, headers=headers), seen)
    with pytest.raises(exc) as e:
        await p.complete(REQ)
    assert e.value.retry_after == retry_after and e.value.retryable == exc.retryable
    assert len(seen) == 1  # SDK retries are off; the gateway owns retries


async def test_anthropic_timeout_mapping() -> None:
    def boom(r: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("t", request=r)

    with pytest.raises(errors.ProviderTimeoutError):
        await _anthropic(boom, []).complete(REQ)


def _sse(*events: dict[str, object]) -> bytes:
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


async def test_anthropic_stream_text_and_citations() -> None:
    start = {
        **_message(),
        "content": [],
        "stop_reason": None,
        "usage": {"input_tokens": 50, "output_tokens": 1},
    }
    stream = _sse(
        {"type": "message_start", "message": start},
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "text", "text": "", "citations": None},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "citations_delta", "citation": CITATION},
        },
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Cla"}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "im."}},
        {"type": "content_block_stop", "index": 0},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": 9},
        },
        {"type": "message_stop"},
    )
    p = _anthropic(
        lambda r: httpx2.Response(200, content=stream, headers={"content-type": "text/event-stream"}), []
    )
    events = [ev async for ev in p.stream(REQ)]
    assert "".join(e.text for e in events if isinstance(e, TextDelta)) == "Claim."
    (cd,) = [e for e in events if isinstance(e, CitationDelta)]
    assert cd.block_index == 0 and cd.citation.end_block_index == 1
    final = events[-1]
    assert isinstance(final, ProviderFinal) and final.stop_reason == "end_turn"
    assert (final.usage.input_tokens, final.usage.output_tokens) == (50, 9)


async def test_openai_embeddings() -> None:
    seen: list[httpx2.Request] = []

    def h(r: httpx2.Request) -> httpx2.Response:
        seen.append(r)
        data = [{"object": "embedding", "index": i, "embedding": [float(i)] * 4} for i in (1, 0)]
        return httpx2.Response(
            200,
            json={
                "object": "list",
                "data": data,
                "model": "text-embedding-3-small",
                "usage": {"prompt_tokens": 6, "total_tokens": 6},
            },
        )

    client = openai.AsyncOpenAI(
        api_key="k",
        max_retries=0,
        http_client=openai.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(h)),
    )
    r = await OpenAIEmbeddingProvider(None, 5.0, client=client).embed("text-embedding-3-small", ["a", "b"], 4)
    assert r.vectors == [[0.0] * 4, [1.0] * 4]  # re-ordered by index
    assert r.usage.input_tokens == 6
    assert json.loads(seen[0].content)["dimensions"] == 4


async def test_openai_rate_limit_mapping() -> None:
    def h(r: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            429, json={"error": {"message": "m", "type": "rate_limit"}}, headers={"retry-after": "1.5"}
        )

    client = openai.AsyncOpenAI(
        api_key="k",
        max_retries=0,
        http_client=openai.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(h)),
    )
    with pytest.raises(errors.RateLimitedError) as e:
        await OpenAIEmbeddingProvider(None, 5.0, client=client).embed("m", ["a"], 4)
    assert e.value.retry_after == 1.5
