# eval: evaluation harness

Run from `backend/` via `make eval*` targets (cost-capped; sample, never run the full set to validate a change).

**Datasets** (`datasets/`): `vault_eval_v1` (122 golden items, schema in `schema.py`, loaded by `loader.py`) and
`vault_eval_v2` (gold schema v2, deterministic train/dev/test split by source doc in `splits_v2.py`).
Third-party sources live in gitignored `sources/` (`make eval-fetch`).

**Runners**: `run.py` iterates items through a pluggable pipeline (`pipeline_http.py` = running stack,
`pipeline_retrieval.py` = retrieval only) and grades with `graders.py`; `--dataset v2` dispatches to `run_v2.py`.
`multiturn.py` and `sse.py` handle multi-turn items and timing the SSE stream. `replay.py` re-ranks stored candidates offline.

**Judging and analysis**: `judge.py` (versioned LLM judge prompts), `rejudge.py` (re-judge stored answers),
`attribution.py` (one earliest faulty stage per failed item), `paired.py` (A/B), `gate.py` (CI gate tiers),
`calibrate_gate.py` / `calibrate_abstain.py` / `support_calibration.py`, `brief_eval.py`, `extraction_check.py`.

**Engine** (`engine/`): stage cache, spend ledger with hard ceiling, run state on disk, batch backend, runner.
`metrics/` holds retrieval and answer metrics; `labels/` the human labels used to calibrate judges.

**Build** (`build/`): scripts that create the datasets (authored/external v1, v2 sampling, audit, coverage check).

**What writes what**: `reports/<run_id>.md|.json` (report.py), `reports/<run_id>.answers.jsonl` (answers_dump.py),
`baselines/<name>.json` (baseline.py `freeze`), `runs/<run_id>/` (engine/state.py), dataset files (build/*).

Invariant: tuning is accepted only through the paired test decision; never tune on test-split content.
