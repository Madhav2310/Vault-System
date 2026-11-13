"""Turn graph runner: routing, step records, failure/timeout, loop guard, Mermaid description (S17.14)."""

import asyncio
import uuid
from typing import Any, cast

from app.chat.graph import END, MAX_NODE_RUNS, TURN_ERROR, Graph, Node, TurnCtx, TurnDeps, TurnState, router
from app.chat.harness import DEFAULT_LIMITS, TurnBudget, TurnLimits


def _ctx(budget: TurnBudget | None = None) -> TurnCtx:
    return TurnCtx(
        cast(TurnDeps, None), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(),
        budget or TurnBudget(DEFAULT_LIMITS),
    )  # fmt: skip


def _mark(name: str, decision: dict[str, Any] | None = None) -> Node:
    async def node(s: TurnState, c: TurnCtx) -> TurnState:
        s.reasons.append(name)
        s.last_decision = decision
        return s

    return node


async def _persist(s: TurnState, c: TurnCtx) -> TurnState:
    s.reasons.append("persist")
    return s


def _graph(nodes: dict[str, Node], edges: dict[str, Any], start: str = "a") -> Graph:
    return Graph({**nodes, "persist": _persist}, edges, start, on_error="persist")


async def test_routing_and_steps() -> None:
    @router("b", "c")
    def pick(s: TurnState) -> str:
        return "c" if s.question == "go c" else "b"

    g = _graph(
        {"a": _mark("a", {"x": 1}), "b": _mark("b"), "c": _mark("c")},
        {"a": pick, "b": "persist", "c": "persist", "persist": END},
    )
    s = await g.run(TurnState("go c"), _ctx())
    assert s.reasons == ["a", "c", "persist"]
    assert [st.node for st in s.steps] == ["a", "c", "persist"]
    assert [st.seq for st in s.steps] == [1, 2, 3]
    assert all(st.status == "ok" and st.duration_ms is not None for st in s.steps)
    assert s.steps[0].decision == {"x": 1} and s.steps[1].decision is None
    assert s.failure is None
    s2 = await g.run(TurnState("go b"), _ctx())
    assert s2.reasons == ["a", "b", "persist"]


async def test_failure_and_timeout() -> None:
    class Boom(Exception):
        error_type = "BOOM"

    async def bad(s: TurnState, c: TurnCtx) -> TurnState:
        raise Boom

    async def slow(s: TurnState, c: TurnCtx) -> TurnState:
        await asyncio.sleep(5)
        return s

    g = _graph({"a": bad, "b": slow, "never": _mark("never")}, {"a": "never", "b": "never"})
    s = await g.run(TurnState("q"), _ctx())
    assert s.status == "failed" and s.failure == (TURN_ERROR, "BOOM")
    assert [(st.node, st.status, st.error_code) for st in s.steps] == [
        ("a", "failed", "BOOM"),
        ("persist", "ok", None),
    ]
    assert "never" not in s.reasons and s.reasons == ["persist"]

    tight = TurnBudget(TurnLimits(4, 0.05, 0))
    g2 = _graph({"a": _mark("a"), "b": slow}, {"a": "b"})
    s2 = await g2.run(TurnState("q"), _ctx(tight))
    assert [(st.node, st.status) for st in s2.steps] == [("a", "ok"), ("b", "timeout"), ("persist", "ok")]
    assert s2.status == "failed" and s2.failure is not None and s2.failure[0] == TURN_ERROR


async def test_loop_guard() -> None:
    g = _graph({"a": _mark("a"), "b": _mark("b")}, {"a": "b", "b": "a"})
    s = await g.run(TurnState("q"), _ctx())
    executed = [st.node for st in s.steps if st.node != "persist"]
    assert len(executed) == MAX_NODE_RUNS == 12
    assert s.failure == (TURN_ERROR, "LOOP_GUARD") and s.status == "failed"
    assert s.steps[-1].node == "persist"


def test_describe_mermaid() -> None:
    @router("b", "c", END)
    def pick(s: TurnState) -> str:
        return "b"

    g = _graph({"a": _mark("a"), "b": _mark("b"), "c": _mark("c")}, {"a": pick, "b": "c", "c": "persist"})
    m = g.describe()
    assert m.startswith("flowchart TD")
    for n in ("a", "b", "c", "persist"):
        assert f'{n}["{n}"]' in m
    assert "a --> a__r{pick}" in m  # router drawn as a diamond
    for t in ("b", "c"):
        assert f"a__r --> {t}" in m
    assert "a__r --> end_((end))" in m and "start((start)) --> a" in m
