"""Gateway ledger behavior against the real `llm_calls` table (one row per attempt, own short session)."""

import pathlib
import time
import uuid
from collections.abc import AsyncIterator
from decimal import Decimal

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.db.models.ledger import LlmCall
from app.db.models.users import User
from app.llm import errors
from app.llm.gateway import DbLedgerWriter
from app.llm.providers import fake
from app.llm.types import Usage
from tests.support.llm import Answer, SleepRecorder, make_gateway

OK = fake.structured({"answer": "yes", "confidence": 0.5}, usage=Usage(1_000, 200, 3_000, 400))


@pytest.fixture
async def owner(engine: AsyncEngine) -> AsyncIterator[uuid.UUID]:
    sm = async_sessionmaker(engine, expire_on_commit=False)
    uid = uuid.uuid4()
    async with sm() as s:
        s.add(User(id=uid, email=f"{uid}@test", api_key_hash=f"h-{uid}"))
        await s.commit()
    yield uid
    async with sm() as s:
        await s.execute(delete(LlmCall).where(LlmCall.owner_id == uid))
        await s.execute(delete(User).where(User.id == uid))
        await s.commit()


async def _rows(engine: AsyncEngine, owner: uuid.UUID) -> list[LlmCall]:
    async with async_sessionmaker(engine)() as s:
        res = await s.execute(
            select(LlmCall).where(LlmCall.owner_id == owner).order_by(LlmCall.created_at, LlmCall.id)
        )
        return list(res.scalars())


def _gw(tmp: pathlib.Path, engine: AsyncEngine, script: list[fake.Step], real_sleep: bool = False):  # type: ignore[no-untyped-def]
    return make_gateway(
        tmp, script, ledger=DbLedgerWriter(async_sessionmaker(engine)), sleep=SleepRecorder(real=real_sleep)
    )


async def _call(gw, owner: uuid.UUID, **kw):  # type: ignore[no-untyped-def]
    return await gw.call(
        **kw,
        purpose="analyze_query",
        lane="interactive",
        prompt=("t_schema", 1),
        owner_id=owner,
        schema=Answer,
        inputs={"q": "x"},
    )


async def test_success_one_row(tmp_path: pathlib.Path, engine: AsyncEngine, owner: uuid.UUID) -> None:
    gw, *_ = _gw(tmp_path, engine, [OK])
    result = await _call(gw, owner)
    (row,) = await _rows(engine, owner)
    assert row.id == result.call_ids[0]
    assert (row.status, row.error_type, row.attempt) == ("ok", None, 1)
    assert (row.purpose, row.lane, row.provider, row.model) == (
        "analyze_query",
        "interactive",
        "fake",
        "gpt-6-luna",  # analyze_query routes to role fast
    )
    assert (row.prompt_id, row.prompt_version) == ("t_schema", 1)
    assert (row.input_tokens, row.output_tokens, row.cache_read_tokens, row.cache_write_tokens) == (
        1000,
        200,
        3000,
        400,
    )
    # gpt-6-luna: (1000*0.10 + 200*0.50 + 3000*0.01 + 400*0.125) / 1e6
    assert row.cost_usd == Decimal("0.000280") == result.cost_usd
    assert row.latency_ms is not None and row.latency_ms >= 0


async def test_retry_after_honored(tmp_path: pathlib.Path, engine: AsyncEngine, owner: uuid.UUID) -> None:
    gw, prov, _, slp = _gw(tmp_path, engine, [fake.rate_limited(retry_after=0.3), OK], real_sleep=True)
    t0 = time.monotonic()
    result = await _call(gw, owner)
    elapsed = time.monotonic() - t0
    rows = await _rows(engine, owner)
    assert [(r.attempt, r.status, r.error_type) for r in rows] == [
        (1, "error", "rate_limited"),
        (2, "ok", None),
    ]
    assert slp.delays[0] >= 0.3 and elapsed >= 0.3
    assert (rows[1].created_at - rows[0].created_at).total_seconds() >= 0.3
    assert result.attempts == 2 and len(prov.requests) == 2


async def test_non_retryable_no_retry(tmp_path: pathlib.Path, engine: AsyncEngine, owner: uuid.UUID) -> None:
    gw, prov, _, slp = _gw(tmp_path, engine, [fake.bad_request(), OK])
    with pytest.raises(errors.BadRequestError) as exc:
        await _call(gw, owner)
    assert exc.value.status_code == 400
    rows = await _rows(engine, owner)
    assert [(r.attempt, r.status, r.error_type) for r in rows] == [(1, "error", "bad_request")]
    assert len(prov.requests) == 1 and slp.delays == []


async def test_retry_cap(tmp_path: pathlib.Path, engine: AsyncEngine, owner: uuid.UUID) -> None:
    gw, prov, _, slp = _gw(tmp_path, engine, [fake.overloaded(), fake.timeout(), fake.rate_limited(0.0), OK])
    with pytest.raises(errors.RateLimitedError):
        await _call(gw, owner, model="gpt-6-luna")  # a pinned model never falls back
    rows = await _rows(engine, owner)
    assert [(r.attempt, r.error_type) for r in rows] == [
        (1, "overloaded"),
        (2, "timeout"),
        (3, "rate_limited"),
    ]
    # error statuses (the provider refused) are free; a timeout may be in progress, so it is estimated
    assert [(r.status, r.cost_usd > 0) for r in rows] == [
        ("error", False),
        ("estimated", True),
        ("error", False),
    ]
    assert len(prov.requests) == 3 and len(slp.delays) == 2
    # no retry-after → jittered exponential backoff: base 0.5 s, rng 0.5 → 0.375 s then 0.75 s
    assert slp.delays == [pytest.approx(0.375), pytest.approx(0.75)]


FAILS = [fake.overloaded(), fake.timeout(), fake.rate_limited(0.0)]


async def test_role_fallback(tmp_path: pathlib.Path, engine: AsyncEngine, owner: uuid.UUID) -> None:
    gw, prov, _, slp = _gw(tmp_path, engine, [*FAILS, OK])
    result = await _call(gw, owner)  # analyze_query: role fast → fallback role balanced
    rows = await _rows(engine, owner)
    assert [(r.attempt, r.role, r.model, r.status) for r in rows] == [
        (1, "fast", "gpt-6-luna", "error"),
        (2, "fast", "gpt-6-luna", "estimated"),  # the timeout: may be in progress, so never free
        (3, "fast", "gpt-6-luna", "error"),
        (4, "balanced", "gpt-6.1-terra", "ok"),
    ]
    assert result.degraded == ("DEGRADED_MODEL_FALLBACK",) and result.model == "gpt-6.1-terra"
    assert result.attempts == 4 and len(prov.requests) == 4 and len(slp.delays) == 2  # no sleep before it
    assert [r.model for r in prov.requests] == ["gpt-6-luna"] * 3 + ["gpt-6.1-terra"]
    assert prov.requests[3].effort == "none"  # balanced supports the node's effort

    # a second failure on the fallback propagates; there is no further fallback
    gw2, prov2, _, _ = _gw(tmp_path, engine, [*FAILS, fake.overloaded(), OK])
    with pytest.raises(errors.OverloadedError):
        await _call(gw2, owner)
    assert len(prov2.requests) == 4


async def test_no_fallback_on_bad_request(
    tmp_path: pathlib.Path, engine: AsyncEngine, owner: uuid.UUID
) -> None:
    gw, prov, _, _ = _gw(tmp_path, engine, [fake.bad_request(), OK])
    with pytest.raises(errors.BadRequestError):
        await _call(gw, owner)
    assert len(prov.requests) == 1  # a 400 would fail on any model


async def test_row_has_role_and_resolved_model(
    tmp_path: pathlib.Path, engine: AsyncEngine, owner: uuid.UUID
) -> None:
    gw, _, _, _ = _gw(tmp_path, engine, [OK])
    await _call(gw, owner)
    (row,) = await _rows(engine, owner)
    assert (row.role, row.model, row.resolved_model) == ("fast", "gpt-6-luna", "gpt-6-luna")
    async with async_sessionmaker(engine)() as s:
        cols = await s.execute(
            text("SELECT column_name FROM information_schema.columns WHERE table_name = 'llm_calls'")
        )
        assert {"role", "resolved_model"} <= {c for (c,) in cols}
