"""Locate loop: bounds, validation, dedup, routing eligibility (pure; no database, no model)."""

import uuid
from types import SimpleNamespace
from typing import Any, cast

import pytest

from app.chat.graph import TurnCtx, TurnState
from app.chat.harness import DEFAULT_LIMITS, TurnBudget
from app.chat.locate import (
    MAX_STEPS,
    Hit,
    LocateOut,
    LoopResult,
    View,
    assemble,
    neighbour_window,
    run_loop,
)
from app.chat.nodes.locate import is_long, low_confidence, skip_reason
from app.chat.turn import after_gate
from app.llm.errors import BudgetExceededError
from app.retrieval.scope import Scope


def hit(n: int, tokens: int = 100) -> Hit:
    return Hit(uuid.UUID(int=n), n, tokens, 1, "S", f"text {n}")


class Store:
    def __init__(self, n: int = 50) -> None:
        self.n, self.calls = n, []  # type: ignore[var-annotated]

    async def search(self, query: str) -> list[Hit]:
        self.calls.append(("search", query))
        return [hit(i) for i in (10, 11, 12)]

    async def find(self, term: str) -> list[Hit]:
        self.calls.append(("find", term))
        return [hit(20)]

    async def open(self, chunk_id: uuid.UUID, k: int) -> list[Hit]:
        self.calls.append(("open", k))
        lo, hi = neighbour_window(chunk_id.int, k, self.n - 1)
        return [hit(i) for i in range(lo, hi + 1)]

    async def owns(self, chunk_id: uuid.UUID) -> Hit | None:
        return hit(chunk_id.int) if chunk_id.int < self.n else None


def script(*outs: LocateOut) -> Any:
    it = iter(outs)
    seen: list[View] = []

    async def decide(view: View) -> LocateOut:
        seen.append(view)
        return next(it)

    decide.seen = seen  # type: ignore[attr-defined]
    return decide


def out(action: str, query: str | None = None, chunk: str | None = None, k: int | None = None) -> LocateOut:
    return LocateOut.model_construct(action=action, query=query, chunk=chunk, k=k)


async def test_loop_bounded() -> None:
    calls = 0
    budget = TurnBudget(DEFAULT_LIMITS)  # 4 model calls in total

    async def decide(view: View) -> LocateOut:
        nonlocal calls
        calls += 1
        budget.charge_call()
        return out("search", f"q{calls}")

    # never more than 4 steps even when the model never says done
    store = Store()
    res = await run_loop(store, decide, [hit(1)], max_steps=99)
    assert res.stop == "max_steps" and len(res.log) == MAX_STEPS == 4 and calls == 4
    assert all(
        e["action"] == "search" and set(e) == {"step", "action", "arg_digest", "new_chunks"} for e in res.log
    )
    assert [e["step"] for e in res.log] == [1, 2, 3, 4]

    # an exhausted budget ends the loop cleanly (no raise)
    calls = 0
    tight = TurnBudget(DEFAULT_LIMITS)
    tight.calls_used = DEFAULT_LIMITS.max_llm_calls - 2

    async def decide2(view: View) -> LocateOut:
        tight.charge_call()
        return out("find", "revenue")

    r2 = await run_loop(Store(), decide2, [hit(1)])
    assert r2.stop == "budget" and len(r2.log) == 2

    # log never carries the query text
    assert "q1" not in str(res.log) and "text" not in str(res.log)


async def test_invalid_action_terminates() -> None:
    store = Store()
    r = await run_loop(store, script(out("delete_all", "x"), out("search", "never")), [hit(1)])
    assert r.stop == "invalid" and store.calls == [] and r.log[0]["action"] == "invalid" and r.found == []
    for bad in (out("search", None), out("search", " "), out("find", "x" * 300), out("open", None, None, 1)):
        rr = await run_loop(Store(), script(bad), [hit(1)])
        assert rr.stop == "invalid"
    for k in (0, 3, -1):
        assert (await run_loop(Store(), script(out("open", None, "C1", k)), [hit(1)])).stop == "invalid"


async def test_chunk_outside_document_rejected() -> None:
    store = Store(n=50)
    outside = str(uuid.UUID(int=999))  # not a ref, a uuid the document does not contain
    r = await run_loop(store, script(out("open", None, outside, 1)), [hit(1)])
    assert r.stop == "invalid" and store.calls == []
    r = await run_loop(store, script(out("open", None, "C9", 1)), [hit(1)])  # an unknown ref
    assert r.stop == "invalid"
    ok = await run_loop(Store(), script(out("open", None, str(uuid.UUID(int=7)), 1), out("done")), [hit(1)])
    assert ok.stop == "done" and len(ok.found) == 3


async def test_dedup_and_new_counts() -> None:
    store = Store()
    r = await run_loop(
        store,
        script(out("search", "aa"), out("search", "bb"), out("find", "cc"), out("done")),
        [hit(10)],  # also returned by search: already known, so not new
    )
    assert [e["new_chunks"] for e in r.log] == [2, 0, 1, 0]
    assert [h.chunk_id.int for h in r.found] == [11, 12, 20]
    ids = assemble([hit(10), hit(11)], r.found, 10_000)
    assert len(ids) == len(set(ids)) == 4 and ids[:3] == [h.chunk_id for h in r.found]
    capped = assemble([hit(10)], [hit(11), hit(12)], 250)
    assert len(capped) == 2


def test_neighbour_bounds() -> None:
    assert neighbour_window(0, 2, 99) == (0, 2)
    assert neighbour_window(99, 2, 99) == (97, 99)
    assert neighbour_window(5, 1, 99) == (4, 6)
    assert neighbour_window(0, 2, 0) == (0, 0)


async def test_model_error_and_deadline_stop_cleanly() -> None:
    async def boom(view: View) -> LocateOut:
        raise BudgetExceededError("x")

    assert (await run_loop(Store(), boom, [])).stop == "budget"
    r = await run_loop(Store(), script(out("done")), [], remaining_s=lambda: 1.0)
    assert r.stop == "deadline" and r.log == []


def _ctx(high: float = 0.5) -> TurnCtx:
    retr = SimpleNamespace(config=SimpleNamespace(gate=SimpleNamespace(high=high)))
    return TurnCtx(
        cast(Any, SimpleNamespace(retriever=retr)), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(),
        TurnBudget(DEFAULT_LIMITS),
    )  # fmt: skip


def _state(entries: int = 1, score: float | None = 0.1, **over: Any) -> TurnState:
    s = TurnState("q")
    s.scope = Scope(entries=tuple((uuid.uuid4(), uuid.uuid4()) for _ in range(entries)))
    s.chunks = [object()]
    s.retrieval = cast(
        Any,
        SimpleNamespace(
            candidates=[SimpleNamespace(rerank_score=score, vector_distance=None, id_match=False)]
        ),
    )
    for k, v in over.items():
        setattr(s, k, v)
    return s


def test_routing_only_one_long_doc_and_low_confidence() -> None:
    assert skip_reason(_state(), _ctx()) is None  # one doc, weak score
    assert skip_reason(_state(entries=2), _ctx()) == "scope"
    assert skip_reason(_state(compare=True), _ctx()) == "route"
    assert skip_reason(_state(mode="full_context"), _ctx()) == "route"
    assert skip_reason(_state(route="summarize"), _ctx()) == "route"
    assert skip_reason(_state(score=0.9), _ctx()) == "confident"
    assert skip_reason(_state(score=0.9, gate_abstain=True), _ctx()) is None  # the gate abstained
    assert low_confidence(_state(score=None), 0.5) is False
    assert is_long(150, 0, 150, 60) and is_long(3, 60, 150, 60) and not is_long(149, 59, 150, 60)


def test_edge_taken_only_with_the_flag() -> None:
    s = _state()
    assert after_gate(s) == "generate"  # flag off: the old edge, whatever the scope
    s.gate_abstain = True
    assert after_gate(s) == "abstain_no_evidence"
    s.locate_on = True
    assert after_gate(s) == "locate"
    s.locate_done = True
    assert after_gate(s) == "abstain_no_evidence"
    assert after_gate(_state(entries=2, locate_on=True)) == "generate"
    assert after_gate(_state(compare=True, locate_on=True)) == "generate"


def test_loop_result_defaults() -> None:
    r = LoopResult()
    assert r.found == [] and r.log == [] and r.stop == "none"


@pytest.mark.parametrize("n", [0, 1])
def test_assemble_keeps_first(n: int) -> None:
    ids = assemble([], [hit(1, 99_999)], 10)
    assert len(ids) == 1
