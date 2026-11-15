import asyncio
import json
import pathlib
import uuid

import pytest

from app.llm import errors
from app.llm.gateway import MemoryLedger
from app.llm.providers import fake
from app.llm.providers.anthropic import build_params
from app.llm.types import ChatRequest, Citation, CitationDelta, Final, SearchResult, TextDelta
from tests.support.llm import Answer, FakeBudget, make_gateway

OWNER = uuid.UUID(int=1)
SR = (SearchResult(source="chunk-1", title="Doc A", sentences=("First sentence.", "Second sentence.")),)


async def _schema_call(gw, **kw):  # type: ignore[no-untyped-def]
    return await gw.call(
        purpose="t",
        lane="interactive",
        prompt=("t_schema", 1),
        owner_id=OWNER,
        schema=Answer,
        inputs={"q": "x"},
        **kw,
    )


# --- AC5 -------------------------------------------------------------------------------------------


async def test_schema_validation(tmp_path: pathlib.Path) -> None:
    gw, _, led, _ = make_gateway(
        tmp_path,
        [
            fake.structured({"answer": "yes", "confidence": 0.9}),
            fake.structured({"answer": 5}),
            fake.text("not json"),
        ],
    )
    ok = await _schema_call(gw)
    assert ok.output == Answer(answer="yes", confidence=0.9)

    for _ in range(2):  # wrong shape, then non-JSON
        with pytest.raises(errors.SchemaValidationError) as exc:
            await _schema_call(gw)
        assert "not json" not in str(exc.value)  # never echoes model output
    assert isinstance(led, MemoryLedger)
    assert [(r.status, r.error_type) for r in led.records] == [
        ("ok", None),
        ("error", "schema_invalid"),
        ("error", "schema_invalid"),
    ]
    assert all(r.attempt == 1 for r in led.records)  # schema failures are not retried


# --- AC6 / AC7 -------------------------------------------------------------------------------------


async def test_stop_reason_refusal(tmp_path: pathlib.Path) -> None:
    gw, prov, led, _ = make_gateway(tmp_path, [fake.refusal("bio")])
    with pytest.raises(errors.RefusalError) as exc:
        await _schema_call(gw)
    assert exc.value.category == "bio"
    assert len(prov.requests) == 1  # not retried
    assert isinstance(led, MemoryLedger)
    (row,) = led.records
    assert (row.status, row.error_type, row.usage.input_tokens) == ("error", "refusal", 100)


async def test_stop_reason_max_tokens(tmp_path: pathlib.Path) -> None:
    gw, prov, led, _ = make_gateway(tmp_path, [fake.max_tokens('{"answer": "trunc')])
    with pytest.raises(errors.TruncatedError):
        await _schema_call(gw)
    assert len(prov.requests) == 1
    assert isinstance(led, MemoryLedger)
    assert [(r.status, r.error_type) for r in led.records] == [("error", "max_tokens")]


# --- AC11 ------------------------------------------------------------------------------------------


async def test_citations_and_format_mutually_exclusive(tmp_path: pathlib.Path) -> None:
    gw, prov, led, _ = make_gateway(tmp_path)
    # 1. the gateway refuses before any provider call or ledger row
    with pytest.raises(errors.RequestConflictError):
        await gw.call(
            purpose="t",
            lane="interactive",
            prompt=("t_schema", 1),
            owner_id=OWNER,
            schema=Answer,
            inputs={"q": "x"},
            search_results=SR,
        )
    with pytest.raises(errors.RequestConflictError):  # citations prompt cannot take a schema either
        await gw.call(
            purpose="t",
            lane="interactive",
            prompt=("t_cite", 1),
            owner_id=OWNER,
            schema=Answer,
            messages=[{"role": "user", "content": "q"}],
            search_results=SR,
        )
    assert prov.requests == []
    assert isinstance(led, MemoryLedger) and led.records == []

    # 2. the request type itself cannot hold both
    with pytest.raises(errors.RequestConflictError):
        ChatRequest(
            model="m",
            system="s",
            messages=[{"role": "user", "content": "q"}],
            max_tokens=10,
            effort="low",
            output_schema={"type": "object"},
            search_results=SR,
        )

    # 3. every request the gateway sends, rendered to the Anthropic wire shape, has at most one of them
    prov.push(fake.structured({"answer": "a", "confidence": 1}))
    await _schema_call(gw)
    await gw.call(
        purpose="t",
        lane="interactive",
        prompt=("t_cite", 1),
        owner_id=OWNER,
        messages=[{"role": "user", "content": "q"}],
        search_results=SR,
    )
    for req in prov.requests:
        params = build_params(req)
        has_format = "format" in params["output_config"]
        body = json.dumps(params["messages"])
        has_citations = '"citations": {"enabled": true}' in body
        assert not (has_format and has_citations)
    schema_params, cite_params = (build_params(r) for r in prov.requests)
    assert schema_params["output_config"]["format"]["type"] == "json_schema"
    blocks = cite_params["messages"][-1]["content"]
    assert blocks[0]["type"] == "search_result"
    assert blocks[0]["content"] == [
        {"type": "text", "text": "First sentence."},
        {"type": "text", "text": "Second sentence."},
    ]  # one text block per sentence
    assert blocks[-1] == {"type": "text", "text": "q"}


# --- AC12 ------------------------------------------------------------------------------------------


async def test_effort_from_frontmatter(tmp_path: pathlib.Path) -> None:
    gw, prov, _, _ = make_gateway(tmp_path, [fake.structured({"answer": "a", "confidence": 1})])
    await _schema_call(gw)  # t_schema@1: effort low
    await gw.call(
        purpose="t",
        lane="interactive",
        prompt=("t_cite", 1),
        owner_id=OWNER,
        messages=[{"role": "user", "content": "q"}],
        search_results=SR,
    )  # t_cite@1: effort high
    async for _ in gw.stream(  # t_text@1: effort medium, streaming path
        purpose="t", lane="interactive", prompt=("t_text", 1), owner_id=OWNER, inputs={"q": "x"}
    ):
        pass
    assert [r.effort for r in prov.requests] == ["low", "high", "medium"]
    assert [build_params(r)["output_config"]["effort"] for r in prov.requests] == ["low", "high", "medium"]
    assert all(
        "thinking" not in build_params(r) and "temperature" not in build_params(r) for r in prov.requests
    )


# --- beyond the ACs: streaming, budget, embeddings -------------------------------------------------


async def test_stream_yields_text_citations_final(tmp_path: pathlib.Path) -> None:
    c = Citation(search_result_index=0, start_block_index=0, end_block_index=1, cited_text="First sentence.")
    gw, _, led, _ = make_gateway(tmp_path, [fake.overloaded(), fake.cited(("Claim one.", [c]))])
    events = [
        ev
        async for ev in gw.stream(
            purpose="generate",
            lane="interactive",
            prompt=("t_cite", 1),
            owner_id=OWNER,
            messages=[{"role": "user", "content": "q"}],
            search_results=SR,
        )
    ]
    assert "".join(e.text for e in events if isinstance(e, TextDelta)) == "Claim one."
    assert [e.citation for e in events if isinstance(e, CitationDelta)] == [c]
    assert isinstance(events[-1], Final) and len(events[-1].call_ids) == 2
    assert isinstance(led, MemoryLedger)
    assert [(r.attempt, r.status, r.error_type) for r in led.records] == [
        (1, "error", "overloaded"),
        (2, "ok", None),
    ]
    assert led.records[1].ttft_ms is not None


async def test_stream_not_retried_after_first_token(tmp_path: pathlib.Path) -> None:
    broken = fake.text("a b c d", fail_after_events=2, fail_with=fake.timeout())
    gw, prov, led, _ = make_gateway(tmp_path, [broken])
    got: list[object] = []
    with pytest.raises(errors.ProviderTimeoutError):
        async for ev in gw.stream(
            purpose="generate", lane="interactive", prompt=("t_text", 1), owner_id=OWNER, inputs={"q": "x"}
        ):
            got.append(ev)
    assert len(got) == 2 and len(prov.requests) == 1
    assert isinstance(led, MemoryLedger) and [r.error_type for r in led.records] == ["timeout"]


async def test_stream_refusal_raises_after_row(tmp_path: pathlib.Path) -> None:
    gw, _, led, _ = make_gateway(tmp_path, [fake.refusal(None)])
    with pytest.raises(errors.RefusalError):
        async for _ in gw.stream(
            purpose="generate", lane="interactive", prompt=("t_text", 1), owner_id=OWNER, inputs={"q": "x"}
        ):
            pass
    assert isinstance(led, MemoryLedger) and [r.error_type for r in led.records] == ["refusal"]


async def test_budget_charged_once_and_deadline_stops_retry(tmp_path: pathlib.Path) -> None:
    gw, prov, _, _ = make_gateway(
        tmp_path, [fake.rate_limited(0.0), fake.structured({"answer": "a", "confidence": 1})]
    )
    b = FakeBudget(max_calls=1)
    await _schema_call(gw, budget=b)
    assert b.calls == 1 and len(b.usage) == 1  # one logical call, usage from the successful attempt
    with pytest.raises(errors.BudgetExceededError):
        await _schema_call(gw, budget=b)
    assert len(prov.requests) == 2

    gw2, prov2, _, _ = make_gateway(tmp_path, [fake.rate_limited(60)])
    with pytest.raises(errors.RateLimitedError):  # retry-after would overrun the deadline → no retry
        await _schema_call(gw2, budget=FakeBudget(deadline_s=5), model="gpt-6.1-terra")  # pinned: no fallback
    assert len(prov2.requests) == 1


async def test_long_retry_after_without_deadline_is_not_slept_in_process(tmp_path: pathlib.Path) -> None:
    """Without a turn deadline (ingestion), a retry-after above `llm_retry_after_max_s` raises instead of
    parking the worker slot for that long; the job layer retries later. Shorter ones are still honoured."""
    gw, prov, _, slp = make_gateway(tmp_path, [fake.rate_limited(3600)])
    with pytest.raises(errors.RateLimitedError):
        await gw.embed(purpose="embed_chunks", lane="background", texts=["a"], owner_id=OWNER)
    assert slp.delays == [] and len(prov.embed_calls) == 1

    gw2, _, _, slp2 = make_gateway(tmp_path, [fake.rate_limited(5.0)])
    await gw2.embed(purpose="embed_chunks", lane="background", texts=["a"], owner_id=OWNER)
    assert slp2.delays == [5.25]  # retry-after plus jitter (rng 0.5 x base 0.5)


async def test_embed(tmp_path: pathlib.Path) -> None:
    gw, prov, led, _ = make_gateway(tmp_path, [fake.rate_limited(0.0)])
    r = await gw.embed(purpose="embed_chunks", lane="background", texts=["a b", "c"], owner_id=OWNER)
    assert len(r.vectors) == 2 and all(len(v) == 1536 for v in r.vectors)
    assert r.usage.input_tokens == 3  # cost rounds to $0.000000 at 3 tokens; formula covered in test_pricing
    assert isinstance(led, MemoryLedger)
    assert [(x.provider, x.model, x.status) for x in led.records] == [
        ("fake", "text-embedding-3-small", "error"),
        ("fake", "text-embedding-3-small", "ok"),
    ]
    with pytest.raises(errors.RequestConflictError):
        await gw.embed(purpose="e", lane="background", texts=["x"] * 129, owner_id=OWNER)
    assert len(prov.embed_calls) == 2


async def test_unknown_model_price_fails_before_call(tmp_path: pathlib.Path) -> None:
    gw, prov, _, _ = make_gateway(tmp_path)
    with pytest.raises(errors.UnknownModelPriceError):
        await _schema_call(gw, model="claude-unknown-1")
    assert prov.requests == []


async def test_no_fallback_after_deadline(tmp_path: pathlib.Path) -> None:
    gw, prov, _, _ = make_gateway(tmp_path, [fake.overloaded(), fake.overloaded(), fake.overloaded()])
    with pytest.raises(errors.OverloadedError):
        await _schema_call(gw, budget=FakeBudget(deadline_s=0))  # deadline already passed: no fallback
    assert [r.model for r in prov.requests] == ["gpt-6-luna"]  # and no retry either


async def test_stream_stalled_before_first_token_is_retried(tmp_path: pathlib.Path) -> None:
    gw, prov, led, _ = make_gateway(tmp_path, [fake.text("a b c"), fake.text("a b c")])
    gw.settings.llm_first_token_timeout_s = 0.05
    real, calls = prov._next, 0

    async def hang_once() -> object:
        nonlocal calls
        calls += 1
        if calls == 1:
            await asyncio.sleep(5)  # the first call never produces a token
        return await real()

    prov._next = hang_once  # type: ignore[method-assign]
    got = [
        ev
        async for ev in gw.stream(
            purpose="generate", lane="interactive", prompt=("t_text", 1), owner_id=OWNER, inputs={"q": "x"}
        )
    ]
    assert len(prov.requests) == 2 and got
    assert isinstance(led, MemoryLedger) and [r.error_type for r in led.records] == ["timeout", None]


async def test_stream_first_token_timeout_off_waits_for_the_provider(tmp_path: pathlib.Path) -> None:
    gw, prov, _, _ = make_gateway(tmp_path, [fake.text("a b")])
    gw.settings.llm_first_token_timeout_s = 0
    prov.latency_s = 0.1
    got = [
        ev
        async for ev in gw.stream(
            purpose="generate", lane="interactive", prompt=("t_text", 1), owner_id=OWNER, inputs={"q": "x"}
        )
    ]
    assert got and len(prov.requests) == 1
