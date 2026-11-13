# Handoff: start of plan v3 coding (2026-11-13)

**For the coding session (Sonnet):**
1. Read `CLAUDE.md`.
2. Read `docs/plan/v3.md`, the execution guide: rules, commands, root-cause notes, codebase map and 12 known gotchas.
3. Read the slice file you work on: `docs/plan/S16.md` … `S20.md`.

Do not read the older S0–S15 files except where a v3 task points to one.

## State (updated after S16 work, 2026-11-13)
- `main` committed. `make ci` 330 passed. `make test` (Docker up): 669 passed, 1 flaky (F-048 trace test, passes alone). `make c1-check` 19/19 in 36 s.
- Docker Desktop is set to 4 CPUs / 6 GB and RUNNING with `make up` (default stack). Reranker image build was started (`docker compose --profile rerank build reranker`); the container benchmark (S16.02 AC6) is the last open S16 Docker check.
- S16 done: 01 (AC5 user-confirmed already), 02 (AC6 pending), 03, 04, 05, 06 (AC3 quickstart run pending), 08 (AC5 time-to-ready table pending). S16.07 gate not yet run: finish AC6s, refresh docs/STATUS.md, report to the user, STOP.
- S17 started: S17.01 done; S17.12 partial (AC1, AC3, AC5 tests still to write: integrity on FakeProvider, `test_call_ids_reported` in tests/integration/eval/test_pipeline_http_spend.py, `test_budget_stop`).
- Next per v3.md section 2: S17.14 turn graph, then S17.02 (re-baseline retrieval with the fixed reranker, `make up-rerank`), S17.03 ...
- Lesson: never overlap test runs against the shared test DB; one heavy command at a time.

## Order
**Follow the execution sequence table in `docs/plan/v3.md` §2 literally.** It covers every task from S16.02 to S20.08, with Docker needs, lanes and stop points. The first three tasks need no Docker:
1. S16.02
2. S16.03
3. S16.06

Then S16.01: ask the user to set Docker Desktop to 6 GB / 4 CPUs and start it.

## Lanes
At most two at once. A lane gets a brief built from the task text, with:
- its owned files and the files not to touch
- the ACs as the test contract
- the gate commands and the report format
- the rule to run targeted tests and only one full gate (see CLAUDE.md "Parallel work" and "Machine hygiene")

Good lane candidates:
- S16.05 (alongside S16.03/S16.06)
- S17.04, S17.11
- S18.08–S18.11
- S19.*

## Stop points for the user
- At every slice gate (S16.07, S17.13, S18.12, S19.07, S20.08).
- When a [T] target misses: record the measured value and stop.
- When a fix fails twice: write the root-cause analysis and stop.
- Before any live spend above the caps written in the task.
