# eval: evaluation harness

Run from `backend/` via `make eval*` targets (cost-capped; sample, never run the full set to validate a change).
The method, the results and the runbook are in `docs/evaluation.md`.

**Datasets** (`datasets/`): `vault_eval_v1` (122 golden items, schema in `schema.py`, loaded by `loader.py`) and
`vault_eval_v2` (gold schema v2, deterministic train/dev/test split by source doc in `splits_v2.py`; the audit
record is `datasets/vault_eval_v2/audit_queue.yaml`). Third-party sources live in gitignored `sources/` (`make eval-fetch`).

**Runners**: `run.py` iterates items through a pluggable pipeline (`pipeline_http.py` = running stack,
`pipeline_retrieval.py` = retrieval only) and grades with `graders.py`; `--dataset v2` dispatches to `run_v2.py`.
`multiturn.py` and `sse.py` handle multi-turn items and timing the SSE stream. `replay.py` re-ranks stored candidates offline.

**Judging and analysis**: `judge.py` (versioned LLM judge prompts), `rejudge.py` (re-judge stored answers; writes
beside its input or in `--out-dir`, never replaces a result without `--overwrite`), `attribution.py` (one earliest
faulty stage per failed item), `paired.py` (A/B; `--ledger` lists the items gained and lost), `gate.py` (CI gate
tiers), `fingerprint.py` (what a run measured, so only like runs are compared), `diagnose.py` (where a run's gold
passages were lost), `calibrate_gate.py` / `calibrate_abstain.py` / `support_calibration.py`, `brief_eval.py`,
`extraction_check.py`.

**Contamination** (`contamination.py`): no default prompt or judge may share a word 5-gram or a proper name with any
gold item, in any split (`python -m eval.contamination`; in CI through `backend/tests/unit/eval/test_contamination.py`).

`metrics/` holds retrieval and answer metrics; `labels/` the human labels used to calibrate judges.

**Build** (`build/`): scripts that create the datasets (authored/external v1, v2 sampling, audit, coverage check).

**What writes what**: `reports/<run_id>.md|.json` (report.py), `reports/<run_id>.answers.jsonl` (answers_dump.py),
`reports/<run_id>.rejudged.json` (rejudge.py), `baselines/<name>.json` (baseline.py `freeze`), dataset files (build/*).

Invariant: a behaviour change is accepted only through a paired dev-split decision; never tune on test-split content.
