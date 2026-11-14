"""A multi-turn item runs as ONE session with N turns, in order, and each turn is scored."""

import pathlib
import uuid
from collections.abc import Callable
from typing import Any

from eval.run import Harness, run_suite
from eval.schema import EvalContext, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.chat.sessions import create_session
from tests.support.evalkit import ScriptedPipeline, Tracker, chunk, item, out

C1 = chunk("c1", "d1", "The Agreement is governed by the laws of Virginia. Payment is due in 30 days.")
C2 = chunk(
    "c2",
    "d1",
    "Either party may terminate on 90 days notice. This Agreement renews yearly.",
    page=2,
    ordinal=1,
)


def _gold(quote: str, page: int) -> dict[str, Any]:
    return {"quote": quote, "page": page, "match": "exact", "score": 100, "doc_id": "d1"}


MT = item(
    id="toy:mt",
    category="multi_turn",
    grading="judge_per_turn",
    doc_ids=None,
    question=None,
    expected_answer=None,
    gold=[],
    turns=[
        {"question": "Which law governs the agreement?", "expected_answer": "Virginia",
         "gold": [_gold("The Agreement is governed by the laws of Virginia.", 1)]},
        {"question": "And how long is the notice period for it?", "expected_answer": "90 days",
         "gold": [_gold("Either party may terminate on 90 days notice.", 2)]},
        {"question": "Does it renew?", "expected_answer": "yearly",
         "gold": [_gold("This Agreement renews yearly.", 2)]},
    ],
)  # fmt: skip
TURNS = [
    out(("Virginia.", [("c1", 0, 1)]), retrieved=["c1"]),
    out(("90 days.", [("c1", 1, 2)]), retrieved=["c1", "c2"]),  # retrieved right, cited the wrong chunk
    out(("Yes, yearly.", [("c2", 1, 2)]), retrieved=["c2"]),
]


async def test_turns_scored(
    sm: async_sessionmaker[AsyncSession],
    tracker: Tracker,
    make_owner: Callable[[], Any],
    tmp_path: pathlib.Path,
) -> None:
    owner = await make_owner()
    tracker.session_owner_ids.append(owner.id)
    opened: list[uuid.UUID] = []

    async def new_session(q: Query, ctx: EvalContext) -> uuid.UUID:
        async with sm() as s:
            row = await create_session(s, ctx.owner_id or owner.id, None, f"eval:{q.item_id}")
        opened.append(row.id)
        return row.id

    pipe = ScriptedPipeline({"toy:mt": TURNS})
    h = Harness(pipe, {"d1": [C1, C2]}, EvalContext(owner_id=owner.id), new_session)
    run, _ = await run_suite([MT], h, name="toy", suite="full", split="all", sm=sm, report_dir=tmp_path)
    tracker.run_ids.append(run.run_id)

    # one session, N=3 turns asked in order, every call in that session with the growing history
    assert len(opened) == 1
    assert [q.turn_index for q, _ in pipe.calls] == [0, 1, 2] and {q.n_turns for q, _ in pipe.calls} == {3}
    assert [q.question for q, _ in pipe.calls] == [t.question for t in MT.turns or []]
    assert pipe.session_ids == [opened[0]] * 3 and pipe.history_lens == [0, 1, 2]
    async with sm() as s:
        n = (
            await s.execute(text("SELECT count(*) FROM chat_sessions WHERE owner_id = :o"), {"o": owner.id})
        ).scalar_one()
    assert n == 1

    # each turn scored on its own gold
    (res,) = run.items
    assert [t.item_id for t in res.turns] == ["toy:mt#t1", "toy:mt#t2", "toy:mt#t3"]
    t1, t2, t3 = (t.metrics for t in res.turns)
    assert (t1["cite_chunk_recall"], t1["cite_sent_recall"], t1["recall@8"]) == (1.0, 1.0, 1.0)
    assert (t2["cite_chunk_recall"], t2["cite_sent_recall"], t2["recall@8"]) == (
        0.0,
        0.0,
        1.0,
    )  # wrong chunk cited
    assert (t3["cite_chunk_recall"], t3["cite_sent_recall"]) == (1.0, 1.0)
    assert res.metrics["cite_chunk_recall"] == 2 / 3  # item level = mean over turns
    assert t2["mrr@40"] == 0.5 and res.metrics["abstain_correct"] == 1.0

    # persisted: item rows and per-turn rows
    async with sm() as s:
        ids = set(
            (
                await s.execute(
                    text("SELECT DISTINCT question_id FROM eval_results WHERE eval_run_id = :i"),
                    {"i": run.run_id},
                )
            ).scalars()
        )
    assert ids == {"toy:mt", "toy:mt#t1", "toy:mt#t2", "toy:mt#t3"}


async def test_failing_turn_does_not_hide_or_stop_the_item(
    sm: async_sessionmaker[AsyncSession], tracker: Tracker, tmp_path: pathlib.Path
) -> None:
    pipe = ScriptedPipeline({"toy:mt": TURNS}, raises={"toy:mt#t2"})
    run, path = await run_suite(
        [MT],
        Harness(pipe, {"d1": [C1, C2]}),
        name="toy",
        suite="full",
        split="all",
        sm=sm,
        report_dir=tmp_path,
    )
    tracker.run_ids.append(run.run_id)
    (res,) = run.items
    assert len(pipe.calls) == 3  # turn 3 still ran
    assert res.turns[1].error and res.turns[1].metrics["abstain_correct"] == 0.0
    assert res.turns[2].error is None and run.n_errors == 1
    assert res.error is not None and res.error.startswith("t2:")
    assert pipe.session_ids == [None] * 3  # no session factory: the pipeline sees no session
    assert "toy:mt#t2" in path.read_text()
