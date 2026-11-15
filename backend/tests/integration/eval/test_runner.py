"""A retrieval run on a 3-item toy set writes one eval_runs row, one eval_results row per item per metric, a
Markdown report and a sidecar that mirrors the metrics; a pipeline error is scored, never dropped."""

import pathlib

from eval.report import RunResult, Sidecar
from eval.run import Harness, run_suite
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.support.evalkit import ScriptedPipeline, Tracker, chunk, item, out

C1 = chunk("c1", "d1", "The Agreement is governed by the laws of Virginia. Payment is due in 30 days.")
C2 = chunk("c2", "d1", "Notices go to the Company.", ordinal=1)
UNITS = {
    "d1": [
        "The Agreement is governed by the laws of Virginia.",
        "Payment is due in 30 days.",
        "Notices go to the Company.",
    ]
}
LOOKUP = item(id="toy:lookup")
PAYMENT = item(
    id="toy:payment",
    question="When is payment due?",
    required_claims=["Payment is due in 30 days."],
    gold_evidence=[{"doc": "d1", "sentence_start": 1, "sentence_end": 2}],
)
UNANS = item(
    id="toy:unans", stratum="unanswerable", answer_type="abstain", required_claims=[], gold_evidence=[]
)
ITEMS = [LOOKUP, PAYMENT, UNANS]
OUTPUTS = {
    "toy:lookup": out(retrieved=["c1", "c2"]),
    "toy:payment": out(retrieved=["c2"] * 8 + ["c1"]),  # gold at rank 9: a recall@8 miss, mrr 1/9
    "toy:unans": out(retrieved=["c2"]),
}


async def _run(
    sm: async_sessionmaker[AsyncSession],
    tracker: Tracker,
    tmp_path: pathlib.Path,
    raises: set[str] | None = None,
) -> tuple[RunResult, pathlib.Path, ScriptedPipeline]:
    pipe = ScriptedPipeline(OUTPUTS, raises=raises)
    h = Harness(pipe, {"d1": [C1, C2]}, gold_units=UNITS.get)
    run, path = await run_suite(
        ITEMS, h, name="toy", suite="retrieval", split="all", sm=sm, report_dir=tmp_path, git_sha="abc1234"
    )
    tracker.run_ids.append(run.run_id)
    return run, path, pipe


async def test_writes_rows(
    sm: async_sessionmaker[AsyncSession], tracker: Tracker, tmp_path: pathlib.Path
) -> None:
    run, _, pipe = await _run(sm, tracker, tmp_path)
    assert [q.item_id for q, _ in pipe.calls] == ["toy:lookup", "toy:payment", "toy:unans"]  # input order
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
    assert runs[0].config == {
        "name": "toy", "suite": "retrieval", "split": "all", "n_items": 3, "dataset": "vault_eval"
    }  # fmt: skip
    got = {(r.question_id, r.metric): r.value for r in rows}
    want = {(i.item_id, m): v for i in run.items for m, v in i.metrics.items()}
    assert len(rows) == len(got) == len(want) and got == want  # one row per item per metric, values as graded
    assert got[("toy:lookup", "recall@8")] == 1.0 and got[("toy:payment", "recall@8")] == 0.0
    assert abs(got[("toy:payment", "mrr@40")] - 1 / 9) < 1e-9
    assert not any(q == "toy:unans" for q, _ in got)  # no gold, no retrieval metric


async def test_report_and_sidecar_written(
    sm: async_sessionmaker[AsyncSession], tracker: Tracker, tmp_path: pathlib.Path
) -> None:
    run, path, _ = await _run(sm, tracker, tmp_path)
    assert path == tmp_path / f"{run.run_id}.md" and path.is_file()
    md = path.read_text()
    assert md.startswith(f"# Eval run {run.run_id}") and "retrieval metrics only" in md
    assert "## Totals" in md and "| recall@8 | 2 | 0.500 |" in md
    per_item = md.split("## Per item")[1]
    for it in ITEMS:
        assert sum(1 for ln in per_item.splitlines() if ln.startswith(f"| {it.id} |")) == 1
    data = Sidecar.model_validate_json((tmp_path / f"{run.run_id}.json").read_text())
    assert [i.item_id for i in data.items] == [i.item_id for i in run.items]
    assert all(i.metrics == r.metrics for i, r in zip(data.items, run.items, strict=True))
    assert data.items[0].quote_hits == [True] and data.items[1].quote_hits == [False]  # per gold range


async def test_pipeline_error_is_scored_not_dropped(
    sm: async_sessionmaker[AsyncSession], tracker: Tracker, tmp_path: pathlib.Path
) -> None:
    run, path, _ = await _run(sm, tracker, tmp_path, raises={"toy:lookup"})
    bad = run.items[0]
    assert bad.error == "turn 1: RuntimeError: scripted failure for toy:lookup" and run.n_errors == 1
    assert bad.metrics["recall@8"] == 0.0  # the empty output is scored
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
