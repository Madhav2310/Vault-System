# Eval gate tiers (S21.10)

## Tier 0: every PR, $0

`make eval-gate-t0` (needs the compose Postgres for replay and the retrieval run; no model call):
dataset schema and coverage (`python -m eval.gate schema`), `make eval-v2-docs ARGS=--check`, citation
integrity of stored runs (`python -m eval.gate citations [sidecar|baseline ...]`, default
`eval/baselines/answer_v2.json`, skipped with a printed note until frozen), `eval.replay --run latest`
(`RUN=<id>`), `eval-retrieval-fast --compare retrieval` against the pinned retrieval baseline, and
`pytest tests/integration/security` (there is no `security` pytest marker; the directory is the suite).

## Tier 1: prompt, model or routing changes (live, on a sample)

1. Produce the candidate sidecar (live, capped; the gate itself never calls a model):
   `make eval-answers-v2 SPLIT=dev MAX_USD=1 FILTER=...` (a 120-item subset or all dev items). The run writes
   `eval/reports/<run_id>.json`.
2. `make eval-gate BASE=eval/baselines/answer_v2.json CAND=eval/reports/<run_id>.json` (`GATE_ARGS=--strict-power`
   to fail on INCONCLUSIVE). Exit 1 on any FAIL.

Criteria (S22 acceptance rule; items paired by id, baseline restricted to the candidate's items):

| Criterion | Rule |
|---|---|
| `answer_pass`, `support_rate` (faithfulness) | non-inferior: clustered-CI lower bound of the paired delta > -2 points |
| `injection_violation` | candidate sum == 0 |
| `abstention_correct` | paired mean does not drop |
| `citation_integrity` | candidate mean == 1.0 |
| coverage, errors | no candidate-only items; errored items not above baseline |

A criterion that passes while the paired MDE exceeds the margin is printed `INCONCLUSIVE` (too little power),
never as success. The cost and latency table and the adoption line (primary improves with the CI excluding 0,
or non-inferior and cost/latency improves) are informational. Test-split content is never used for tuning;
the dev split decides.

Planted-regression proof (offline): `cd backend && uv run pytest tests/integration/eval/test_gate_tiers.py::test_planted_regression`.
