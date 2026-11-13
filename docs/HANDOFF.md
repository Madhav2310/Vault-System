# Handoff: start of plan v3 coding (2026-11-13)

**For the coding session (Sonnet):**
1. Read `CLAUDE.md`.
2. Read `docs/plan/v3.md`, the execution guide: rules, commands, root-cause notes, codebase map and 12 known gotchas.
3. Read the slice file you work on: `docs/plan/S16.md` … `S20.md`.

Do not read the older S0–S15 files except where a v3 task points to one.

## State
- **`main`**: clean, committed. `make ci`: 316 passed.
- **`make test` not re-run after the last merge.** Docker was stopped on purpose by the user (it crashed Chrome). That re-run is the first job of S16.01, step 5.
- **Docker is OFF.** Keep it off unless a task needs it (v3.md §3). `make ci` needs no Docker.
- **Spend to date:** about $3.
- **Research:** `reports/Vault rebuild strategy research.md`.
- **Status:** `docs/STATUS.md`.
- **Root-cause analysis** of the reranker and extraction: v3.md §6, measured natively on this machine.

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
