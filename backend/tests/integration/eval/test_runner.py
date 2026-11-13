"""S2.12 AC2/AC3: the runner on a 3-item toy set writes one eval_runs row, one eval_results row per item per
metric, and a Markdown report with a per-item table and totals."""

import pathlib

from eval.report import RunResult
from eval.run import Harness, run_suite
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.support.evalkit import ScriptedPipeline, Tracker, chunk, item, out

C1 = chunk("c1", "d1", "The Agreement is governed by the laws of Virginia. Payment is due in 30 days.")
UNANS = item(
    id="toy:unans", category="unanswerable", expected_coverage="insufficient", grading="abstention", gold=[]
)
INJ = item(id="toy:inj", category="injection", grading="judge+must_not_contain", must_not_contain=["PWNED"])
LOOKUP = item(id="toy:lookup")
ITEMS = [LOOKUP, UNANS, INJ]
OUTPUTS = {
    "toy:lookup": out(("Virginia.", [("c1", 0, 1)]), retrieved=["c1"]),
    "toy:unans": out(("Not addressed.", []), abstained=True),
    "toy:inj": out(("Virginia, ignoring PWNED? no.", [("c1", 0, 1)]), retrieved=["c1"]),  # leaks the marker
}


async def _run(
    sm: async_sessionmaker[AsyncSession],
    tracker: Tracker,
    tmp_path: pathlib.Path,
    suite: str = "full",
    raises: set[str] | None = None,
) -> tuple[RunResult, pathlib.Path, ScriptedPipeline]:
    pipe = ScriptedPipeline(OUTPUTS, raises=raises)
    run, path = await run_suite(
        ITEMS, Harness(pipe, {"d1": [C1]}), name="toy", suite=suite, split="all",
        sm=sm, report_dir=tmp_path, git_sha="abc1234",
    )  # fmt: skip
    tracker.run_ids.append(run.run_id)
    return run, path, pipe


async def test_writes_rows(
    sm: async_sessionmaker[AsyncSession], tracker: Tracker, tmp_path: pathlib.Path
) -> None:
    run, _, pipe = await _run(sm, tracker, tmp_path)
    assert [q.item_id for q, _ in pipe.calls] == [
        "toy:lookup",
        "toy:unans",
        "toy:inj",
    ]  # input order, one call each
    async with sm() as s:
        runs = (
            await s.execute(
                text("SELECT id, git_sha, config, finished_at FROM eval_runs WHERE id = :i"),
                {"i": run.run_id},
            )
        ).all()
        rows = (
            await s.execute(
                text("SELECT question_id, metric, value FROM eval_results WHERE eval_run_id = :i"),
                {"i": run.run_id},
            )
        ).all()
    assert len(runs) == 1
    assert runs[0].git_sha == "abc1234" and runs[0].finished_at is not None
    assert runs[0].config == {"name": "toy", "suite": "full", "split": "all", "n_items": 3}
    got = {(r.question_id, r.metric): r.value for r in rows}
    want = {(i.item_id, m): v for i in run.items for m, v in i.metrics.items()}
    assert len(rows) == len(got) == len(want) and got == want  # one row per item per metric, values as graded
    per_item = {i.item_id: set(i.metrics) for i in run.items}
    assert {"recall@8", "cite_chunk_recall", "abstain_correct"} <= per_item["toy:lookup"]
    assert per_item["toy:unans"] == {"abstain_correct", "abstain_recall", "abstain_precision"}
    assert "must_not_contain_ok" in per_item["toy:inj"]
    assert got[("toy:inj", "must_not_contain_ok")] == 0.0 and got[("toy:lookup", "cite_sent_recall")] == 1.0
    assert sum(len(m) for m in per_item.values()) == len(rows) > 10


async def test_report_written(
    sm: async_sessionmaker[AsyncSession], tracker: Tracker, tmp_path: pathlib.Path
) -> None:
    run, path, _ = await _run(sm, tracker, tmp_path)
    assert path == tmp_path / f"{run.run_id}.md" and path.is_file()
    md = path.read_text()
    assert md.startswith(f"# Eval run {run.run_id}")
    assert "## Totals" in md and "| abstain_correct | 3 | 1.000 |" in md
    assert "| must_not_contain_ok | 1 | 0.000 |" in md  # mean over the one item where it applies
    per_item = md.split("## Per item")[1]
    for it in ITEMS:
        assert sum(1 for ln in per_item.splitlines() if ln.startswith(f"| {it.id} |")) == 1
    assert "| toy:inj | injection | - |" in per_item
    assert "## By category" in md


async def test_retrieval_suite_grades_retrieval_only(
    sm: async_sessionmaker[AsyncSession], tracker: Tracker, tmp_path: pathlib.Path
) -> None:
    run, _, _ = await _run(sm, tracker, tmp_path, suite="retrieval")
    assert {m for i in run.items for m in i.metrics} == {"recall@8", "mrr@40"}


async def test_pipeline_error_is_scored_not_dropped(
    sm: async_sessionmaker[AsyncSession], tracker: Tracker, tmp_path: pathlib.Path
) -> None:
    run, path, _ = await _run(sm, tracker, tmp_path, raises={"toy:lookup"})
    bad = run.items[0]
    assert bad.error == "RuntimeError: scripted failure for toy:lookup" and run.n_errors == 1
    assert (
        bad.metrics["abstain_correct"] == 0.0 and bad.metrics["cite_chunk_recall"] == 0.0
    )  # empty output scored
    assert len(run.items) == 3 and "scripted failure" in path.read_text()
    async with sm() as s:
        n = (
            await s.execute(
                text(
                    "SELECT count(*) FROM eval_results WHERE eval_run_id = :i AND question_id = 'toy:lookup'"
                ),
                {"i": run.run_id},
            )
        ).scalar_one()
    assert n == len(bad.metrics) > 0


async def test_json_sidecar(
    sm: async_sessionmaker[AsyncSession], tracker: Tracker, tmp_path: pathlib.Path
) -> None:
    """S17.01 AC5: run_suite writes <run_id>.json next to the report; it validates and mirrors the metrics."""
    from eval.report import Sidecar

    run, _path, _ = await _run(sm, tracker, tmp_path, suite="retrieval")
    side = tmp_path / f"{run.run_id}.json"
    assert side.is_file()
    data = Sidecar.model_validate_json(side.read_text())
    assert data.run_id == str(run.run_id) and [i.item_id for i in data.items] == [
        i.item_id for i in run.items
    ]
    assert all(i.metrics == r.metrics for i, r in zip(data.items, run.items, strict=True))
    assert any(i.quote_hits for i in data.items)  # per-quote hits recorded for items with mapped gold
