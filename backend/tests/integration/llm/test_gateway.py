"""Gateway ledger behavior against the real `llm_calls` table (one row per attempt, own short session)."""

import pathlib
import time
import uuid
from collections.abc import AsyncIterator
from decimal import Decimal

import pytest
from sqlalchemy import delete, select
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
        res = await s.execute(select(LlmCall).where(LlmCall.owner_id == owner).order_by(LlmCall.attempt))
        return list(res.scalars())


def _gw(tmp: pathlib.Path, engine: AsyncEngine, script: list[fake.Step], real_sleep: bool = False):  # type: ignore[no-untyped-def]
    return make_gateway(
        tmp, script, ledger=DbLedgerWriter(async_sessionmaker(engine)), sleep=SleepRecorder(real=real_sleep)
    )


async def _call(gw, owner: uuid.UUID):  # type: ignore[no-untyped-def]
    return await gw.call(
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
        "claude-haiku-5-5",
    )
    assert (row.prompt_id, row.prompt_version) == ("t_schema", 1)
    assert (row.input_tokens, row.output_tokens, row.cache_read_tokens, row.cache_write_tokens) == (
        1000,
        200,
        3000,
        400,
    )
    # Haiku 5.5: (1000*0.10 + 200*0.50 + 3000*0.01 + 400*0.125) / 1e6
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
        await _call(gw, owner)
    rows = await _rows(engine, owner)
    assert [(r.attempt, r.error_type) for r in rows] == [
        (1, "overloaded"),
        (2, "timeout"),
        (3, "rate_limited"),
    ]
    assert all(r.status == "error" and r.cost_usd == 0 for r in rows)
    assert len(prov.requests) == 3 and len(slp.delays) == 2
    # no retry-after → jittered exponential backoff: base 0.5 s, rng 0.5 → 0.375 s then 0.75 s
    assert slp.delays == [pytest.approx(0.375), pytest.approx(0.75)]
