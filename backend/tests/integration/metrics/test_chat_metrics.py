"""GET /v1/metrics/chat: values from a seeded ledger, cost reconciliation, empty state, isolation."""

import uuid
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import text

AT = "now() - make_interval(secs => CAST(:m AS float8) * 60)"


async def seed_session(sm: Any, owner_id: uuid.UUID) -> uuid.UUID:
    sid = uuid.uuid4()
    async with sm() as s:
        await s.execute(
            text("INSERT INTO chat_sessions (id, owner_id) VALUES (:i, :o)"), {"i": sid, "o": owner_id}
        )
        await s.commit()
    return sid


async def run(
    sm: Any,
    owner_id: uuid.UUID,
    session_id: uuid.UUID,
    *,
    minutes_ago: float,
    latency: int | None,
    ttft: int | None,
    cost: str,
    degraded: list[str] | None = None,
    replay_of: uuid.UUID | None = None,
) -> uuid.UUID:
    rid = uuid.uuid4()
    async with sm() as s:
        await s.execute(
            text(
                "INSERT INTO rag_runs (id, owner_id, session_id, replay_of_run_id, route, "
                "mode, original_query, status, degraded, cost_usd, latency_ms, ttft_ms, created_at) "
                "VALUES (:id, :o, :s, :rp, 'qa', 'fast', 'q', 'done', :dg, :c, :l, :t, " + AT + ")"
            ),
            {
                "id": rid,
                "o": owner_id,
                "s": session_id,
                "rp": replay_of,
                "dg": degraded or [],
                "c": Decimal(cost),
                "l": latency,
                "t": ttft,
                "m": minutes_ago,
            },
        )
        await s.commit()
    return rid


async def message(
    sm: Any,
    owner_id: uuid.UUID,
    session_id: uuid.UUID,
    *,
    minutes_ago: float,
    role: str = "assistant",
    rtype: str | None = None,
    coverage: str | None = None,
) -> None:
    async with sm() as s:
        await s.execute(
            text(
                "INSERT INTO chat_messages (id, owner_id, session_id, role, content, response_type, "
                "coverage_state, created_at) VALUES (gen_random_uuid(), :o, :s, :r, 'x', :rt, :cv, "
                + AT
                + ")"
            ),
            {"o": owner_id, "s": session_id, "r": role, "rt": rtype, "cv": coverage, "m": minutes_ago},
        )
        await s.commit()


async def call(
    sm: Any,
    owner_id: uuid.UUID,
    run_id: uuid.UUID | None,
    *,
    purpose: str,
    lane: str,
    model: str,
    inp: int,
    out: int,
    cache: int,
    cost: str,
    minutes_ago: float,
) -> None:
    async with sm() as s:
        await s.execute(
            text(
                "INSERT INTO llm_calls (id, owner_id, run_id, purpose, lane, provider, model, attempt, "
                "input_tokens, output_tokens, cache_read_tokens, cost_usd, status, created_at) "
                "VALUES (gen_random_uuid(), :o, :r, :p, :l, 'openai', :md, 1, :i, :ou, :c, :cost, 'ok', "
                + AT
                + ")"
            ),
            {
                "o": owner_id,
                "r": run_id,
                "p": purpose,
                "l": lane,
                "md": model,
                "i": inp,
                "ou": out,
                "c": cache,
                "cost": Decimal(cost),
                "m": minutes_ago,
            },
        )
        await s.commit()


async def seed_owner_a(sm: Any, o: uuid.UUID) -> None:
    sid = await seed_session(sm, o)
    d2 = ["rerank_skipped", "gate_skipped"]
    r1 = await run(
        sm, o, sid, minutes_ago=10, latency=1000, ttft=200, cost="0.010", degraded=["rerank_skipped"]
    )
    r2 = await run(sm, o, sid, minutes_ago=20, latency=2000, ttft=400, cost="0.020", degraded=d2)
    await run(sm, o, sid, minutes_ago=30, latency=3000, ttft=None, cost="0.030")
    await run(sm, o, sid, minutes_ago=40, latency=4000, ttft=800, cost="0.040")
    await run(sm, o, sid, minutes_ago=2 * 1440, latency=100000, ttft=None, cost="9.000")  # 2 days ago
    await run(sm, o, sid, minutes_ago=5, latency=888888, ttft=1, cost="0.600", replay_of=r1)  # not a turn

    await message(sm, o, sid, minutes_ago=10, rtype="answer", coverage="supported")
    await message(sm, o, sid, minutes_ago=20, rtype="answer", coverage="partial")
    await message(sm, o, sid, minutes_ago=30, rtype="abstention", coverage="not_found")
    await message(sm, o, sid, minutes_ago=40, rtype="answer", coverage="supported")
    await message(sm, o, sid, minutes_ago=45, rtype="clarification")
    await message(sm, o, sid, minutes_ago=46, role="user")  # user message: not an outcome
    await message(sm, o, sid, minutes_ago=2 * 1440, rtype="answer", coverage="supported")  # outside 24h

    # (run, purpose, lane, model, input, output, cache_read, cost, minutes ago)
    ledger = [
        (r1, "analyze_query", "interactive", "fast-m", 1000, 100, 400, "0.002", 10),
        (r1, "generate", "interactive", "deep-m", 3000, 500, 1000, "0.008", 10),
        (r2, "generate", "interactive", "deep-m", 2000, 300, 0, "0.006", 20),
        (None, "embed_chunks", "background", "emb-m", 5000, 0, 0, "0.0001", 15),
        (None, "generate", "interactive", "deep-m", 99999, 1, 0, "5", 3 * 1440),  # 3 days ago
    ]
    for rid, purpose, lane, model, inp, out, cache, cost, ago in ledger:
        await call(
            sm,
            o,
            rid,
            purpose=purpose,
            lane=lane,
            model=model,
            inp=inp,
            out=out,
            cache=cache,
            cost=cost,
            minutes_ago=ago,
        )


async def test_values(api: httpx.AsyncClient, sm: Any, mk_owner: Callable[..., Any]) -> None:
    a = await mk_owner()
    b = await mk_owner()
    await seed_owner_a(sm, a.id)
    sb = await seed_session(sm, b.id)
    rb = await run(sm, b.id, sb, minutes_ago=1, latency=1, ttft=1, cost="123.000", degraded=["b_only"])
    await message(sm, b.id, sb, minutes_ago=1, rtype="error", coverage="b_cov")
    await call(
        sm,
        b.id,
        rb,
        purpose="b_purpose",
        lane="interactive",
        model="b-m",
        inp=7,
        out=7,
        cache=7,
        cost="77",
        minutes_ago=1,
    )

    r = await api.get("/v1/metrics/chat", headers=a.auth)
    assert r.status_code == 200
    m = r.json()
    assert m["window"] == "24h" and m["scope"] == "owner"
    # Turns in 24h: runs 10, 20, 30, 40 min ago = 4 (2-day-old run and replay run excluded).
    assert m["turns"] == 4
    # Assistant messages: answer x3 (supported, partial, supported), abstention x1, clarification x1.
    assert m["outcomes"] == {"answer": 3, "abstention": 1, "clarification": 1}
    # coverage_state: supported 2, partial 1, not_found 1, none (clarification) 1.
    assert m["coverage"] == {"supported": 2, "partial": 1, "not_found": 1, "none": 1}
    # latency [1000, 2000, 3000, 4000]: p50 = 2500; p95 rank 0.95 * 3 = 2.85 -> 3000 + 0.85 * 1000 = 3850.
    assert m["latency_ms"] == {"p50": 2500.0, "p95": 3850.0}
    # ttft [200, 400, 800] (one NULL ignored): p50 = 400; p95 rank 1.9 -> 400 + 0.9 * 400 = 760.
    assert m["ttft_ms"] == {"p50": 400.0, "p95": 760.0}
    # cost per turn [0.010, 0.020, 0.030, 0.040]: p50 = 0.025; p95 0.030 + 0.85 * 0.010 = 0.0385.
    assert m["cost_per_turn_usd"]["p50"] == pytest.approx(0.025, abs=1e-6)
    assert m["cost_per_turn_usd"]["p95"] == pytest.approx(0.0385, abs=1e-6)
    # degraded: rerank_skipped in runs 1 and 2 = 2, gate_skipped in run 2 = 1.
    assert m["degraded"] == {"rerank_skipped": 2, "gate_skipped": 1}
    llm = m["llm"]
    # Ledger rows in 24h (the 3-day-old call is out): total cost 0.002 + 0.008 + 0.006 + 0.0001 = 0.0161.
    assert llm["calls"] == 4
    assert llm["total_cost_usd"] == pytest.approx(0.0161, abs=1e-9)
    # cache hit ratio = (400 + 1000 + 0 + 0) / (1000 + 3000 + 2000 + 5000) = 1400 / 11000 = 0.1273 (4 dp).
    assert llm["cache_hit_ratio"] == 0.1273
    by_key = {(t["lane"], t["model"], t["purpose"]): t for t in llm["tokens"]}
    assert set(by_key) == {
        ("interactive", "deep-m", "generate"),
        ("interactive", "fast-m", "analyze_query"),
        ("background", "emb-m", "embed_chunks"),
    }
    g = by_key[
        ("interactive", "deep-m", "generate")
    ]  # two calls: 3000 + 2000 in, 500 + 300 out, 1000 + 0 cached
    assert (g["calls"], g["input_tokens"], g["output_tokens"], g["cache_read_tokens"]) == (2, 5000, 800, 1000)
    assert g["cost_usd"] == pytest.approx(0.014, abs=1e-9)
    an = by_key[("interactive", "fast-m", "analyze_query")]
    assert (an["calls"], an["input_tokens"], an["output_tokens"], an["cache_read_tokens"]) == (
        1,
        1000,
        100,
        400,
    )
    assert llm["cost_by_purpose"] == {
        "generate": {"calls": 2, "cost_usd": pytest.approx(0.014, abs=1e-9)},
        "analyze_query": {"calls": 1, "cost_usd": pytest.approx(0.002, abs=1e-9)},
        "embed_chunks": {"calls": 1, "cost_usd": pytest.approx(0.0001, abs=1e-9)},
    }

    # 7d window adds the 2-day-old run and message and the 3-day-old call.
    m7 = (await api.get("/v1/metrics/chat?window=7d", headers=a.auth)).json()
    assert m7["turns"] == 5
    assert m7["outcomes"]["answer"] == 4
    # latency [1000, 2000, 3000, 4000, 100000]: p50 = 3000; p95 rank 3.8 -> 4000 + 0.8 * 96000 = 80800.
    assert m7["latency_ms"] == {"p50": 3000.0, "p95": 80800.0}
    assert m7["llm"]["calls"] == 5 and m7["llm"]["total_cost_usd"] == pytest.approx(5.0161, abs=1e-9)

    mb = (await api.get("/v1/metrics/chat", headers=b.auth)).json()
    assert mb["turns"] == 1 and mb["outcomes"] == {"error": 1} and mb["coverage"] == {"b_cov": 1}
    assert mb["degraded"] == {"b_only": 1}
    assert mb["llm"]["total_cost_usd"] == 77.0 and mb["llm"]["cache_hit_ratio"] == 1.0
    assert list(mb["llm"]["cost_by_purpose"]) == ["b_purpose"]


async def test_cost_reconciles(api: httpx.AsyncClient, sm: Any, mk_owner: Callable[..., Any]) -> None:
    a = await mk_owner()
    await seed_owner_a(sm, a.id)
    for window, interval in (("1h", "1 hour"), ("24h", "24 hours"), ("7d", "7 days")):
        async with sm() as s:  # independent of the code under test: a plain SUM over the ledger
            expected = (
                await s.execute(
                    text(
                        "SELECT COALESCE(sum(cost_usd), 0) FROM llm_calls "
                        f"WHERE owner_id = :o AND created_at >= now() - interval '{interval}'"
                    ),
                    {"o": a.id},
                )
            ).scalar_one()
        llm = (await api.get(f"/v1/metrics/chat?window={window}", headers=a.auth)).json()["llm"]
        assert llm["total_cost_usd"] == pytest.approx(float(expected), abs=1e-9), window
        # Every grouping of the same rows adds up to the same total.
        assert sum(v["cost_usd"] for v in llm["cost_by_purpose"].values()) == pytest.approx(
            float(expected), abs=1e-9
        )
        assert sum(t["cost_usd"] for t in llm["tokens"]) == pytest.approx(float(expected), abs=1e-9)
    # Hand-computed anchors: 24h = 0.0161, 7d = 0.0161 + 5.
    assert float(expected) == pytest.approx(5.0161, abs=1e-9)


async def test_empty_state(api: httpx.AsyncClient, mk_owner: Callable[..., Any]) -> None:
    a = await mk_owner()
    r = await api.get("/v1/metrics/chat", headers=a.auth)
    assert r.status_code == 200
    assert r.json() == {
        "scope": "owner",
        "window": "24h",
        "quota": {"limit_usd": 1.0, "spent_today_usd": 0.0},
        "turns": 0,
        "outcomes": {},
        "coverage": {},
        "latency_ms": {"p50": None, "p95": None},
        "ttft_ms": {"p50": None, "p95": None},
        "cost_per_turn_usd": {"p50": None, "p95": None},
        "node_latency_ms": {},
        "degraded": {},
        "feedback": {"up": 0, "down": 0, "by_reason": {}},
        "llm": {
            "calls": 0,
            "total_cost_usd": 0.0,
            "cache_hit_ratio": None,
            "tokens": [],
            "cost_by_purpose": {},
        },
    }


async def test_window_validation_scope_and_auth(
    api: httpx.AsyncClient, sm: Any, mk_owner: Callable[..., Any]
) -> None:
    a = await mk_owner()
    admin = await mk_owner(admin=True)
    assert (await api.get("/v1/metrics/chat?window=2h", headers=a.auth)).status_code == 422
    assert (await api.get("/v1/metrics/chat?scope=global", headers=a.auth)).status_code == 403
    sid = await seed_session(sm, a.id)
    await run(sm, a.id, sid, minutes_ago=1, latency=1, ttft=1, cost="1")
    g = await api.get("/v1/metrics/chat?scope=global", headers=admin.auth)
    assert g.status_code == 200 and g.json()["scope"] == "global" and g.json()["turns"] >= 1
    assert (await api.get("/v1/metrics/chat", headers=admin.auth)).json()["turns"] == 0
    assert (await api.get("/v1/metrics/chat")).status_code == 401
