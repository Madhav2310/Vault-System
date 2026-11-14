"""Working-memory window (pure selection; DB loader: integration/chat/test_memory_db.py)."""

from app.chat.memory import Memory, Msg, group_turns, select_window


def words(m: Msg) -> int:
    return len(m.content.split())


def count(s: str) -> int:
    return len(s.split())


def turn(n: int, size: int) -> list[Msg]:
    return [Msg("user", " ".join(["q"] * size) + f" {n}"), Msg("assistant", " ".join(["a"] * size))]


def test_window_budget() -> None:
    turns = [turn(i, 10) for i in range(10)]  # 21 "tokens" per turn
    kept = select_window(turns, budget=70, count=count)
    assert len(kept) == 3  # 63 <= 70 < 84
    assert sum(count(m.content) for t in kept for m in t) <= 70
    assert kept == turns[-3:]  # the newest, in chronological order
    assert select_window(turns, budget=10_000, count=count) == turns


def test_whole_turns() -> None:
    small, big = turn(0, 2), turn(1, 50)
    # the newest turn fits, the big one does not: stop there, never trim it and never skip it
    assert select_window([small, big, small], budget=30, count=count) == [small]
    # a single turn over budget yields nothing, not a truncated turn
    assert select_window([big], budget=30, count=count) == []
    # grouping keeps user+assistant together, orphans first
    msgs = [Msg("assistant", "x"), *turn(0, 1), *turn(1, 1)]
    g = group_turns(msgs)
    assert [len(t) for t in g] == [1, 2, 2] and g[1][0].role == "user"


def test_summary_included() -> None:
    m = Memory(
        turns=[turn(0, 1)],
        rolling_summary="earlier we discussed X",
        resolved_entities={"the agreement": "d1"},
    )
    assert m.rolling_summary == "earlier we discussed X"
    assert m.resolved_entities == {"the agreement": "d1"}
    assert Memory(turns=[]).rolling_summary is None and Memory(turns=[]).resolved_entities == {}
