# v2 eval: first live cycle (runbook)

Ordered commands for the first live pass over `eval/datasets/vault_eval_v2/`. Run from the repo root, one heavy
command at a time (check `uptime`; wait if load is above ~8). Costs: **est.** = estimate from the plan caps
(`docs/plan/S21.md`), nothing here is measured yet; replace with the printed `spent_usd` as each step runs.
STOP = the user decides before the next step.

Every live step has a hard cap (`MAX_USD`); a run that hits the cap exits 3 with a partial result and freezes
nothing. Keys live only in the gitignored `.env`.

## 0. Prerequisites ($0)

| Step | Command | Cost |
|---|---|---|
| Download the source PDFs and write the document map | `make eval-v2-docs ARGS=--download` then `make eval-v2-docs ARGS=--check` | $0 (network only) |
| Stop brief enrichment for eval uploads: add `BRIEF_ENABLED=false` to `.env` (the worker reads `.env` through `env_file`; a shell export does not reach it) | edit `.env` | $0 |
| Start the stack so the worker picks it up | `make up` (or `docker compose up -d --force-recreate worker` on a running stack) | $0 |
| Confirm the worker env | `docker compose exec -T worker printenv BRIEF_ENABLED` must print `false` | $0 |
| Reranker for realistic retrieval (optional, heavier): `make up-rerank`; without it the run is degraded (`no_rerank`) and says so | | $0 |

STOP: do not continue unless the worker prints `false`. `make eval-answers-v2` also prints a reminder when
`.env` and the shell both lack `BRIEF_ENABLED=false`.

## 1. Train run (answers + judges)

```
make eval-answers-v2 SPLIT=train MAX_USD=3
```

- Cost: **est.** up to $3 (cap). Writes `eval/reports/<run_id>.md`, `<run_id>.json` (metrics sidecar) and
  `<run_id>.answers.jsonl` (answer text, cited passages, `failed`; input to steps 2 and 7).
- Note the printed `run <run_id>` line; `ANSWERS=eval/reports/<run_id>.answers.jsonl` below.
- No `--freeze` on train: train is for finding gold and system problems, not for the baseline.

STOP: read the per-stratum table and any `degraded` or errored items before spending on the audit.

## 2. Cross-model gold audit

```
make eval-audit ANSWERS=eval/reports/<run_id>.answers.jsonl MAX_USD=2
```

- Cost: **est.** up to $2 (cap, role `flagship`). Writes `eval/datasets/vault_eval_v2/audit_queue.yaml`; exit 3
  means the cap stopped it (partial queue; unaudited items stay `draft`).
- Re-running refuses to overwrite a queue that already has human decisions (pass `AUDIT_ARGS=--force` only on purpose).
- The audit covers every live v2 item, not just the train split; Vault's answer is read from the answers file,
  so items outside it are audited with the strong model's answer only (`vault_answers_missing` in the queue).
- $0 rehearsal first: `cd backend && PYTHONPATH=.. uv run python -m eval.build.audit --answers ../$ANSWERS --dry-run --limit 5`.

## 3. Human review (no cost)

```
cd backend && PYTHONPATH=.. uv run python -m eval.build.audit --render ../eval/datasets/vault_eval_v2/audit_queue.yaml --render-out ../eval/reports/audit_review.md
```

Fill `decision` for every queued item in `audit_queue.yaml`: `gold_wrong` | `gold_incomplete` (add the full
corrected list as `fix`) | `ambiguous` (item dropped, give `note`) | `system_wrong`.

STOP: human time, the main cost of this cycle. Nothing proceeds until every queued item has a decision.

## 4. Apply the decisions ($0)

```
make eval-audit-apply APPLY_ARGS=--check     # validate and print the summary, writes nothing
make eval-audit-apply                        # surgical edits of the dataset part files
make eval-v2-docs ARGS=--check
```

Refuses (exit 2, nothing written) on a missing decision or a stale queue. Review `git diff eval/datasets/vault_eval_v2`
and commit it before any further run: later runs must see the audited gold.

STOP: user reviews the diff.

## 5. Dev run (tuning feedback)

```
make eval-answers-v2 SPLIT=dev MAX_USD=3
```

- Cost: **est.** up to $3 (cap). No freeze.
- Tuning changes go through the convergence engine's paired test decision; never tune on test-split content.

## 6. Test run and baseline freeze (once)

```
make eval-answers-v2 SPLIT=test MAX_USD=3 EVAL_ARGS="--freeze answer_v2"
```

- Cost: **est.** up to $3 (cap, S21.10 AC1). Writes `eval/baselines/answer_v2.json` with per-stratum table,
  clustered CIs, MDE inputs and attribution.
- Run it only on the audited dataset from step 4 and a configuration you will not change before the freeze.
- Injection check, cheaper: `make eval-answers-v2 SPLIT=test FILTER=stratum=injection MAX_USD=0.5` (est. up to $0.50).

STOP: user accepts or rejects the baseline; a capped (exit 3) run freezes nothing.

## 7. Label export for judge calibration ($0)

```
make eval-labels ANSWERS=eval/reports/<run_id>.answers.jsonl N=200
```

- Cost: $0. Writes `eval/labels/labeled.yaml` (about 200 pairs, about 40% failures, null labels, split by
  `source_doc`). Use the answers file of the run with the most varied outcomes (train or dev; the pairs keep
  their own split tag so test-split pairs stay frozen for calibration).
- Then the human labels (see `eval/labels/README.md`); calibration itself is `python -m eval.judge --calibrate
  eval/labels/labeled.yaml --max-usd 3` (est. up to $3, a separate gate).

STOP: labeling is human time.

## Budget summary

| Step | Cap | Status |
|---|---|---|
| 1 train | $3 | est. |
| 2 audit | $2 | est. |
| 5 dev | $3 | est. |
| 6 test + freeze | $3 | est. |
| 7 export | $0 | exact |
| 7b calibrate (later) | $3 | est. |

Record the real `spent_usd` of each step in `docs/progress.md` (measured) and replace the table.

## Regenerate / verify

- Answers file shape: `cd backend && uv run pytest tests/unit/eval/test_answers_dump.py -q`
- Rows: one JSON object per line with `item_id, question, answer, segments[{text,passages}], cited_chunk_ids,
  abstained, failed, stratum, source_doc`.
