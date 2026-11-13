"""Multi-turn items: one session, N turns in order, each turn scored on its own.

The runner opens ONE session for the item (`new_session`, if the harness provides one), then asks the turns
in order through the same pipeline, passing the growing `history` and the shared `session_id`. A turn that
raises is scored as an empty output (counted against the system) and the next turn still runs. Item-level
metrics are the mean over turns where a metric is defined; per-turn results keep ids `<item>#t<n>`.
"""

from __future__ import annotations

import dataclasses
import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable

from eval.report import ItemResult
from eval.schema import EvalContext, GoldenItem, ItemOutput, Query

Pipeline = Callable[[Query, EvalContext], Awaitable[ItemOutput]]
NewSession = Callable[[Query, EvalContext], Awaitable[uuid.UUID]]
ScoreTurn = Callable[[GoldenItem, ItemOutput], dict[str, float]]


async def run_multiturn(
    item: GoldenItem,
    pipeline: Pipeline,
    base_ctx: EvalContext,
    score_turn: ScoreTurn,
    *,
    split: str,
    new_session: NewSession | None = None,
) -> ItemResult:
    assert item.turns, f"{item.id} has no turns"
    n = len(item.turns)
    ctx = dataclasses.replace(base_ctx, history=[])
    scope = tuple(item.doc_ids) if item.doc_ids else None

    def query(i: int) -> Query:
        return Query(item.id, item.turns[i].question if item.turns else "", item.category, scope, i, n)

    if new_session is not None:
        ctx.session_id = await new_session(query(0), ctx)
    turns: list[ItemResult] = []
    for i in range(n):
        q = query(i)
        turn = item.turn_item(i)
        err: str | None = None
        try:
            out = await pipeline(q, ctx)
        except Exception as e:  # a failing turn must not abort the item or hide the failure
            out, err = ItemOutput(), f"{type(e).__name__}: {e}"
        ctx.history.append((q, out))
        turns.append(ItemResult(turn.id, item.category, split, score_turn(turn, out), err))
    acc: dict[str, list[float]] = defaultdict(list)
    for t in turns:
        for k, v in t.metrics.items():
            acc[k].append(v)
    errors = [f"t{i + 1}: {t.error}" for i, t in enumerate(turns) if t.error]
    return ItemResult(
        item.id,
        item.category,
        split,
        {k: sum(v) / len(v) for k, v in acc.items()},
        "; ".join(errors) or None,
        turns,
    )
